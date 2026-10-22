"""The AgentOS execution graph (LangGraph).

Control flow:

    START → plan → select ─┬→ execute ──→ select   (loop over ready tasks)
                           ├→ pause  → END         (human approval pending)
                           ├→ review ─┬→ select    (bounded revision requested)
                           │          └→ report → END
                           ├→ fail   → END
                           └→ cancel → END

State persistence is application-level (ADR-005): every node writes its results to
PostgreSQL before the graph proceeds, so a run can pause (approval), survive a
process restart, and resume by simply re-invoking the graph — `select` re-derives
the next step from the database every time. LangGraph provides the typed state
schema, conditional routing, and bounded iteration.
"""

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.analyst import run_analyst
from app.agents.definitions import SYSTEM_AGENTS
from app.agents.executor import run_executor
from app.agents.planner import create_plan
from app.agents.reporter import run_reporter
from app.agents.research import run_research
from app.agents.reviewer import run_review, tasks_for_review
from app.agents.reviewer_task import run_review_task
from app.core.config import Settings
from app.core.logging import get_logger
from app.engine.context import AgentContext
from app.engine.errors import BudgetExceededError, EngineError, TaskFailedError
from app.events.recorder import EventRecorder
from app.llm.base import LLMError, LLMProvider, LLMTransientError
from app.models import (
    AgentDefinition,
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    ExecutionMode,
    RunStatus,
    RunTask,
    TaskStatus,
)
from app.schemas.agents import ReviewVerdict
from app.tools.base import ApprovalPendingError, ToolRejectedError, ToolTransientError
from app.tools.registry import ToolRegistry
from app.tools.registry import registry as global_registry

logger = get_logger("agentos.engine")

AGENT_RUNNERS = {
    "research": run_research,
    "analyst": run_analyst,
    "executor": run_executor,
    # Project-level review tasks (PM harness) run through the same engine.
    "reviewer": run_review_task,
}

RETRY_BACKOFF_S = 1.0


class EngineState(TypedDict, total=False):
    run_id: str
    decision: str
    current_ordinal: int
    outcome: str


@dataclass
class ExecutionEnv:
    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    llm: LLMProvider | None
    registry: ToolRegistry
    recorder: EventRecorder


async def _load_run(session: AsyncSession, run_id: str) -> AgentRun:
    run = await session.get(AgentRun, uuid.UUID(run_id))
    if run is None:
        raise EngineError(f"run {run_id} not found")
    return run


async def _emit(
    env: ExecutionEnv, session: AsyncSession, run: AgentRun, type: str, payload: dict | None = None
) -> None:
    await env.recorder.emit(
        session, run_id=run.id, organization_id=run.organization_id, type=type, payload=payload
    )


async def _set_status(
    env: ExecutionEnv, session: AsyncSession, run: AgentRun, status: RunStatus
) -> None:
    if run.status != status:
        old = run.status
        run.status = status
        await _emit(
            env, session, run, "run.status_changed", {"from": old.value, "to": status.value}
        )


async def _allowed_tools(session: AsyncSession, run: AgentRun, agent_key: str) -> list[str]:
    """Allowlist from the DB definition (org override first, then system row),
    falling back to the code spec."""
    definition = await session.scalar(
        sa_select(AgentDefinition)
        .where(
            AgentDefinition.key == agent_key,
            AgentDefinition.enabled.is_(True),
            (AgentDefinition.organization_id == run.organization_id)
            | (AgentDefinition.organization_id.is_(None)),
        )
        .order_by(AgentDefinition.organization_id.desc().nulls_last())
    )
    if definition is not None:
        return list(definition.allowed_tools)
    spec = SYSTEM_AGENTS.get(agent_key)
    return list(spec.allowed_tools) if spec else []


def _llm_for_run(env: ExecutionEnv, run: AgentRun) -> LLMProvider | None:
    # A run created in deterministic mode stays deterministic even if a provider
    # appears later — mode is recorded on the run and shown in the UI.
    return env.llm if run.mode == ExecutionMode.LLM else None


def build_graph(env: ExecutionEnv):
    async def plan_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            if run.plan is not None:
                return {}  # resuming — plan already exists
            await _set_status(env, session, run, RunStatus.PLANNING)
            await session.commit()
            await env.recorder.publish_pending()

            try:
                plan = await create_plan(
                    session,
                    organization_id=run.organization_id,
                    input_text=run.input_text,
                    params=(run.budgets or {}).get("request_params", {}),
                    llm=_llm_for_run(env, run),
                    settings=env.settings,
                )
            except LLMError as exc:
                raise EngineError(f"planning failed: {exc}") from exc
            run.plan = plan.model_dump(mode="json")
            for task in plan.tasks:
                session.add(
                    RunTask(
                        run_id=run.id,
                        organization_id=run.organization_id,
                        ordinal=task.ordinal,
                        agent_key=task.agent,
                        objective=task.objective,
                        depends_on=task.depends_on,
                        input=task.params,
                        mode=run.mode,
                        max_attempts=2,
                    )
                )
            await _emit(env, session, run, "run.plan_created", {"plan": run.plan})
            await _set_status(env, session, run, RunStatus.RUNNING)
            await session.commit()
            await env.recorder.publish_pending()
        return {}

    async def select_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            tasks = (
                await session.scalars(
                    sa_select(RunTask).where(RunTask.run_id == run.id).order_by(RunTask.ordinal)
                )
            ).all()

            if run.cancel_requested:
                return {"decision": "cancel"}

            # Tasks waiting on approval: resume or finalize based on the DB row.
            for task in tasks:
                if task.status != TaskStatus.WAITING_APPROVAL:
                    continue
                approval = await session.scalar(
                    sa_select(ApprovalRequest)
                    .where(ApprovalRequest.task_id == task.id)
                    .order_by(ApprovalRequest.created_at.desc())
                )
                if approval is None or approval.status == ApprovalStatus.PENDING:
                    return {"decision": "pause"}
                if approval.status == ApprovalStatus.APPROVED:
                    task.status = TaskStatus.PENDING  # re-run; registry sees approval
                    await session.commit()
                    return {"decision": "execute", "current_ordinal": task.ordinal}
                task.status = TaskStatus.FAILED
                task.error = f"operation {approval.tool_name} was {approval.status.value}"
                await session.commit()
                return {"decision": "fail"}

            by_ordinal = {t.ordinal: t for t in tasks}
            if any(t.status == TaskStatus.FAILED for t in tasks):
                return {"decision": "fail"}
            for task in tasks:
                if task.status != TaskStatus.PENDING:
                    continue
                deps_done = all(
                    by_ordinal.get(d) is not None
                    and by_ordinal[d].status == TaskStatus.COMPLETED
                    for d in task.depends_on
                )
                if deps_done:
                    return {"decision": "execute", "current_ordinal": task.ordinal}
            if all(t.status == TaskStatus.COMPLETED for t in tasks):
                return {"decision": "review"}
            return {"decision": "fail"}  # unreachable deps → cannot progress

    async def execute_node(state: EngineState) -> EngineState:
        ordinal = state["current_ordinal"]
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            task = await session.scalar(
                sa_select(RunTask).where(RunTask.run_id == run.id, RunTask.ordinal == ordinal)
            )
            if task is None:
                raise EngineError(f"task ordinal {ordinal} missing")
            runner = AGENT_RUNNERS.get(task.agent_key)
            if runner is None:
                task.status = TaskStatus.FAILED
                task.error = f"no runner for agent '{task.agent_key}'"
                await session.commit()
                return {}

            completed = {
                t.ordinal: t.output
                for t in (
                    await session.scalars(
                        sa_select(RunTask).where(
                            RunTask.run_id == run.id, RunTask.status == TaskStatus.COMPLETED
                        )
                    )
                ).all()
                if t.output
            }
            allowed = await _allowed_tools(session, run, task.agent_key)

            while True:
                task.attempts += 1
                task.status = TaskStatus.RUNNING
                task.started_at = task.started_at or dt.datetime.now(dt.UTC)
                await _emit(
                    env, session, run, "task.started",
                    {
                        "ordinal": task.ordinal,
                        "agent": task.agent_key,
                        "objective": task.objective,
                        "attempt": task.attempts,
                    },
                )
                await session.commit()
                await env.recorder.publish_pending()

                ctx = AgentContext(
                    session=session,
                    settings=env.settings,
                    run=run,
                    task=task,
                    recorder=env.recorder,
                    registry=env.registry,
                    llm=_llm_for_run(env, run),
                    allowed_tools=allowed,
                    prior_outputs=completed,
                )
                try:
                    async with asyncio.timeout(env.settings.task_timeout_s):
                        output = await runner(ctx)
                except ApprovalPendingError as exc:
                    task.status = TaskStatus.WAITING_APPROVAL
                    await _set_status(env, session, run, RunStatus.WAITING_APPROVAL)
                    await _emit(
                        env, session, run, "task.waiting_approval",
                        {
                            "ordinal": task.ordinal,
                            "approval_request_id": str(exc.approval_request_id),
                        },
                    )
                    await session.commit()
                    await env.recorder.publish_pending()
                    return {}
                except (ToolTransientError, LLMTransientError, TimeoutError) as exc:
                    reason = "task timed out" if isinstance(exc, TimeoutError) else str(exc)
                    await session.rollback()
                    # Rollback expires loaded objects — re-fetch before touching them.
                    run = await _load_run(session, state["run_id"])
                    task = await session.get(RunTask, task.id)
                    if task.attempts < task.max_attempts:
                        await _emit(
                            env, session, run, "task.retrying",
                            {"ordinal": task.ordinal, "attempt": task.attempts, "error": reason},
                        )
                        await session.commit()
                        await env.recorder.publish_pending()
                        await asyncio.sleep(RETRY_BACKOFF_S * task.attempts)
                        continue
                    await _fail_task(env, session, run, task, reason)
                    return {}
                except (
                    ToolRejectedError,
                    TaskFailedError,
                    BudgetExceededError,
                    LLMError,
                    EngineError,
                ) as exc:
                    await session.rollback()
                    run = await _load_run(session, state["run_id"])
                    task = await session.get(RunTask, task.id)
                    await _fail_task(env, session, run, task, str(exc))
                    return {}

                task.output = output.model_dump(mode="json")
                task.status = TaskStatus.COMPLETED
                task.finished_at = dt.datetime.now(dt.UTC)
                await _emit(
                    env, session, run, "task.completed",
                    {
                        "ordinal": task.ordinal,
                        "agent": task.agent_key,
                        "summary": getattr(output, "summary", "")[:500],
                        "mode": run.mode.value,
                    },
                )
                await session.commit()
                await env.recorder.publish_pending()
                return {}

    async def review_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            await _set_status(env, session, run, RunStatus.REVIEWING)
            tasks = (
                await session.scalars(
                    sa_select(RunTask).where(RunTask.run_id == run.id).order_by(RunTask.ordinal)
                )
            ).all()
            review_task = RunTask(  # transient context row for the reviewer (not persisted)
                run_id=run.id,
                organization_id=run.organization_id,
                ordinal=-1,
                agent_key="reviewer",
                objective="review",
                input={},
                mode=run.mode,
            )
            ctx = AgentContext(
                session=session,
                settings=env.settings,
                run=run,
                task=review_task,
                recorder=env.recorder,
                registry=env.registry,
                llm=_llm_for_run(env, run),
                allowed_tools=await _allowed_tools(session, run, "reviewer"),
            )
            try:
                verdict = await run_review(ctx, tasks_for_review(list(tasks)))
            except (LLMTransientError, LLMError) as exc:
                # Review degradation is never silent: record and proceed with the
                # deterministic verdict baseline (approved=False → caveats in report).
                verdict = ReviewVerdict(
                    approved=True,
                    issues=[],
                    checks_performed=[f"llm-review-skipped: {exc}"],
                )

            budgets = dict(run.budgets or {})
            iterations = int(budgets.get("review_iterations", 0))
            budgets["last_review"] = verdict.model_dump(mode="json")

            decision = "report"
            if (
                not verdict.approved
                and verdict.revision_task_ordinal is not None
                and iterations < env.settings.max_review_iterations
            ):
                target = next(
                    (t for t in tasks if t.ordinal == verdict.revision_task_ordinal), None
                )
                if target is not None:
                    target.status = TaskStatus.PENDING
                    target.output = None
                    target.attempts = 0
                    new_input = dict(target.input or {})
                    new_input["revision_request"] = verdict.revision_request or ""
                    target.input = new_input
                    budgets["review_iterations"] = iterations + 1
                    decision = "revise"

            run.budgets = budgets
            await _emit(
                env, session, run, "review.completed",
                {
                    "approved": verdict.approved,
                    "issues": [i.model_dump() for i in verdict.issues],
                    "checks": verdict.checks_performed,
                    "decision": decision,
                    "iteration": iterations + (1 if decision == "revise" else 0),
                },
            )
            await session.commit()
            await env.recorder.publish_pending()
            return {"decision": decision}

    async def report_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            await _set_status(env, session, run, RunStatus.REPORTING)
            tasks = (
                await session.scalars(
                    sa_select(RunTask)
                    .where(RunTask.run_id == run.id, RunTask.status == TaskStatus.COMPLETED)
                    .order_by(RunTask.ordinal)
                )
            ).all()
            verdict_raw = (run.budgets or {}).get("last_review")
            verdict = ReviewVerdict.model_validate(verdict_raw) if verdict_raw else None
            report_task = RunTask(
                run_id=run.id,
                organization_id=run.organization_id,
                ordinal=-2,
                agent_key="reporter",
                objective="report",
                input={},
                mode=run.mode,
            )
            ctx = AgentContext(
                session=session,
                settings=env.settings,
                run=run,
                task=report_task,
                recorder=env.recorder,
                registry=env.registry,
                llm=_llm_for_run(env, run),
                allowed_tools=await _allowed_tools(session, run, "reporter"),
            )
            _report, report_id = await run_reporter(ctx, list(tasks), verdict)
            run.finished_at = dt.datetime.now(dt.UTC)
            await _set_status(env, session, run, RunStatus.COMPLETED)
            await _emit(
                env, session, run, "run.completed",
                {"report_id": report_id, "mode": run.mode.value},
            )
            await session.commit()
            await env.recorder.publish_pending()
        return {"outcome": "completed"}

    async def pause_node(state: EngineState) -> EngineState:
        # Status was already set by execute_node; this is the graph's exit lane.
        return {"outcome": "waiting_approval"}

    async def cancel_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            for task in (
                await session.scalars(sa_select(RunTask).where(RunTask.run_id == run.id))
            ).all():
                if task.status in (
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                    TaskStatus.WAITING_APPROVAL,
                ):
                    task.status = TaskStatus.CANCELLED
            run.finished_at = dt.datetime.now(dt.UTC)
            await _set_status(env, session, run, RunStatus.CANCELLED)
            await _emit(env, session, run, "run.cancelled", {})
            await session.commit()
            await env.recorder.publish_pending()
        return {"outcome": "cancelled"}

    async def fail_node(state: EngineState) -> EngineState:
        async with env.sessionmaker() as session:
            run = await _load_run(session, state["run_id"])
            failed = next(
                (
                    t
                    for t in (
                        await session.scalars(sa_select(RunTask).where(RunTask.run_id == run.id))
                    ).all()
                    if t.status == TaskStatus.FAILED
                ),
                None,
            )
            run.error = (
                f"task {failed.ordinal} ({failed.agent_key}) failed: {failed.error}"
                if failed
                else (run.error or "run could not progress")
            )
            run.finished_at = dt.datetime.now(dt.UTC)
            await _set_status(env, session, run, RunStatus.FAILED)
            await _emit(env, session, run, "run.failed", {"error": run.error})
            await session.commit()
            await env.recorder.publish_pending()
        return {"outcome": "failed"}

    graph = StateGraph(EngineState)
    graph.add_node("plan", plan_node)
    graph.add_node("select", select_node)
    graph.add_node("execute", execute_node)
    graph.add_node("review", review_node)
    graph.add_node("report", report_node)
    graph.add_node("pause", pause_node)
    graph.add_node("cancel", cancel_node)
    graph.add_node("fail", fail_node)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "select")
    graph.add_conditional_edges(
        "select",
        lambda s: s.get("decision", "fail"),
        {
            "execute": "execute",
            "pause": "pause",
            "review": "review",
            "fail": "fail",
            "cancel": "cancel",
        },
    )
    graph.add_edge("execute", "select")
    graph.add_conditional_edges(
        "review",
        lambda s: s.get("decision", "report"),
        {"revise": "select", "report": "report"},
    )
    graph.add_edge("report", END)
    graph.add_edge("pause", END)
    graph.add_edge("cancel", END)
    graph.add_edge("fail", END)
    return graph.compile()


async def _fail_task(
    env: ExecutionEnv, session: AsyncSession, run: AgentRun, task: RunTask, error: str
) -> None:
    task.status = TaskStatus.FAILED
    task.error = error[:2000]
    task.finished_at = dt.datetime.now(dt.UTC)
    await _emit(
        env, session, run, "task.failed",
        {"ordinal": task.ordinal, "agent": task.agent_key, "error": task.error},
    )
    await session.commit()
    await env.recorder.publish_pending()


def default_env(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> ExecutionEnv:
    import app.tools.builtin  # noqa: F401 — ensure built-ins are registered
    from app.llm.factory import get_llm_provider

    return ExecutionEnv(
        sessionmaker=sessionmaker,
        settings=settings,
        llm=get_llm_provider(),
        registry=global_registry,
        recorder=EventRecorder(),
    )
