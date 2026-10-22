"""Dependency-aware scheduler: readiness refresh, atomic claiming, attempt
execution through the existing run engine, finalization, and crash recovery.

Eligibility is computed here from persisted state — never from model output.
Claiming uses a single conditional UPDATE so concurrent workers cannot take the
same task (Scenario F). Recovery re-queues or fails tasks whose worker died
(Scenario H) without duplicating side effects.
"""

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.models import (
    ACTIVE_RUN_STATUSES,
    ACTIVE_TASK_STATUSES,
    AgentRun,
    ExecutionMode,
    PMTaskStatus,
    Project,
    ProjectStatus,
    ProjectTask,
    RunStatus,
    RunTask,
    TaskDependency,
    TaskStatus,
)
from app.services import acceptance
from app.services.project_events import ProjectEventRecorder
from app.services.projects import (
    eligibility,
    project_tasks,
)
from app.services.task_state import transition

logger = get_logger("agentos.scheduler")

DEFAULT_MAX_PARALLEL = 2
STALE_CLAIM_TTL_S = 900  # worker presumed dead after 15 min without finishing


async def refresh_task_states(
    session: AsyncSession, recorder: ProjectEventRecorder, project: Project
) -> None:
    """Recompute blocked/ready for all schedulable tasks; emit change events."""
    for task in await project_tasks(session, project.id):
        if task.status not in (
            PMTaskStatus.PLANNED,
            PMTaskStatus.BLOCKED,
            PMTaskStatus.READY,
        ):
            continue
        eligible, reason = await eligibility(session, project, task)
        if eligible and task.status != PMTaskStatus.READY:
            transition(task, PMTaskStatus.READY)
            await recorder.emit(
                session,
                project_id=project.id,
                organization_id=project.organization_id,
                type="task.ready",
                payload={"ordinal": task.ordinal, "title": task.title},
            )
        elif not eligible:
            previous_reason = task.blocked_reason
            if task.status == PMTaskStatus.READY:
                transition(task, PMTaskStatus.BLOCKED)
            elif task.status == PMTaskStatus.PLANNED:
                transition(task, PMTaskStatus.BLOCKED)
            task.blocked_reason = reason
            if previous_reason != reason:
                await recorder.emit(
                    session,
                    project_id=project.id,
                    organization_id=project.organization_id,
                    type="task.blocked",
                    payload={"ordinal": task.ordinal, "reason": reason},
                )


async def claim_task(session: AsyncSession, task_id: uuid.UUID, worker_id: str) -> bool:
    """Atomic claim: exactly one caller wins (ready → assigned)."""
    result = await session.execute(
        update(ProjectTask)
        .where(ProjectTask.id == task_id, ProjectTask.status == PMTaskStatus.READY)
        .values(
            status=PMTaskStatus.ASSIGNED,
            claimed_by=worker_id,
            claimed_at=dt.datetime.now(dt.UTC),
            attempts=ProjectTask.attempts + 1,
        )
    )
    await session.commit()
    return result.rowcount == 1


async def schedule_project(project_id: str) -> list[str]:
    """Refresh readiness and dispatch eligible tasks within the parallel limit.
    Returns the ids of tasks dispatched in this pass."""
    sessionmaker = get_sessionmaker()
    recorder = ProjectEventRecorder()
    dispatched: list[str] = []
    async with sessionmaker() as session:
        project = await session.get(Project, uuid.UUID(project_id))
        if project is None or project.status != ProjectStatus.ACTIVE.value:
            return []
        await refresh_task_states(session, recorder, project)
        await session.commit()
        await recorder.publish_pending()

        tasks = await project_tasks(session, project.id)
        active = sum(1 for t in tasks if t.status in ACTIVE_TASK_STATUSES)
        limit = int(project.settings.get("max_parallel_tasks", DEFAULT_MAX_PARALLEL))
        slots = max(0, limit - active)
        ready = sorted(
            (t for t in tasks if t.status == PMTaskStatus.READY),
            key=lambda t: (t.priority, t.ordinal),
        )
        for task in ready[:slots]:
            worker_id = f"dispatch-{uuid.uuid4().hex[:10]}"
            if await claim_task(session, task.id, worker_id):
                dispatched.append(str(task.id))
                from app.worker.tasks import execute_project_task_attempt

                await execute_project_task_attempt.kiq(str(task.id), worker_id)
    return dispatched


async def _build_run_for_task(
    session: AsyncSession, project: Project, task: ProjectTask
) -> AgentRun:
    """Pre-planned single-task AgentRun executed by the existing engine."""
    from app.llm.factory import get_llm_provider

    params = dict(task.params or {})
    # Deliverables from succeeded dependencies, for the agent (esp. reviewer).
    dep_artifacts: list[dict] = []
    succeeded_deps: list[ProjectTask] = []
    dep_rows = (
        await session.scalars(
            select(ProjectTask)
            .join(TaskDependency, TaskDependency.depends_on_task_id == ProjectTask.id)
            .where(TaskDependency.task_id == task.id)
            .order_by(ProjectTask.ordinal)
        )
    ).all()
    for dep in dep_rows:
        if dep.status == PMTaskStatus.SUCCEEDED and dep.result:
            dep_artifacts.extend(dep.result.get("artifacts", []))
            succeeded_deps.append(dep)
    dep_artifacts.extend(params.get("supplied_artifacts", []))
    if dep_artifacts:
        params["dependency_artifacts"] = dep_artifacts
    if task.revision_feedback:
        params["revision_request"] = task.revision_feedback

    mode = ExecutionMode.LLM if get_llm_provider() is not None else ExecutionMode.DETERMINISTIC
    # Dependency outputs enter the run as pre-completed engine tasks so agents
    # that read prior outputs (e.g. executor send_followup) see them natively.
    main_ordinal = len(succeeded_deps)
    plan = {
        "goal": task.title,
        "tasks": [
            *[
                {
                    "ordinal": i,
                    "agent": dep.agent_key or "research",
                    "objective": f"input from project task #{dep.ordinal}: {dep.title}"[:500],
                    "depends_on": [],
                    "params": {},
                }
                for i, dep in enumerate(succeeded_deps)
            ],
            {
                "ordinal": main_ordinal,
                "agent": task.agent_key,
                "objective": task.description or task.title,
                "depends_on": list(range(main_ordinal)),
                "params": params,
            },
        ],
        "notes": f"project task #{task.ordinal} attempt {task.attempts}",
    }
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        created_by=project.created_by,
        title=f"[{project.name}] #{task.ordinal} {task.title}"[:300],
        input_text=task.description or task.title,
        status=RunStatus.QUEUED,
        mode=mode,
        plan=plan,
        budgets={"request_params": params, "project_task_id": str(task.id)},
        correlation_id=uuid.uuid4().hex,
    )
    session.add(run)
    await session.flush()
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    for i, dep in enumerate(succeeded_deps):
        session.add(
            RunTask(
                run_id=run.id,
                organization_id=project.organization_id,
                ordinal=i,
                agent_key=dep.agent_key or "research",
                objective=f"input from project task #{dep.ordinal}: {dep.title}"[:500],
                depends_on=[],
                # Marker: carried-over dependency output, not work of this run —
                # the engine reviewer must not re-validate it (cross-run evidence).
                input={"_pm_dependency_input": True},
                output=(dep.result or {}).get("output"),
                status=TaskStatus.COMPLETED,
                mode=mode,
                started_at=now,
                finished_at=now,
            )
        )
    session.add(
        RunTask(
            run_id=run.id,
            organization_id=project.organization_id,
            ordinal=main_ordinal,
            agent_key=task.agent_key or "",
            objective=task.description or task.title,
            depends_on=list(range(main_ordinal)),
            input=params,
            mode=mode,
            max_attempts=2,
        )
    )
    return run


async def execute_task_attempt(task_id: str, worker_id: str) -> str:
    """Run one claimed attempt end-to-end (worker entrypoint)."""
    sessionmaker = get_sessionmaker()
    recorder = ProjectEventRecorder()
    async with sessionmaker() as session:
        task = await session.get(ProjectTask, uuid.UUID(task_id))
        if task is None:
            return "not_found"
        if task.status != PMTaskStatus.ASSIGNED or task.claimed_by != worker_id:
            logger.info("attempt skipped; claim not held", task_id=task_id)
            return "not_claimed"
        project = await session.get(Project, task.project_id)
        if project is None or project.status != ProjectStatus.ACTIVE.value:
            transition(task, PMTaskStatus.READY)  # release claim
            await session.commit()
            return "project_inactive"

        run = await _build_run_for_task(session, project, task)
        task.run_id = run.id
        transition(task, PMTaskStatus.RUNNING)
        await recorder.emit(
            session,
            project_id=project.id,
            organization_id=project.organization_id,
            type="task.started",
            payload={
                "ordinal": task.ordinal,
                "agent": task.agent_key,
                "attempt": task.attempts,
                "run_id": str(run.id),
            },
        )
        await session.commit()
        await recorder.publish_pending()
        run_id = run.id

    from app.engine.runner import execute_run

    await execute_run(str(run_id))
    return await finalize_task_for_run(run_id)


async def finalize_task_for_run(run_id: uuid.UUID | str) -> str:
    """Map a finished (or paused) project-task run back onto the project task.
    Also called after approval-resumed runs complete (worker hook)."""
    sessionmaker = get_sessionmaker()
    recorder = ProjectEventRecorder()
    async with sessionmaker() as session:
        run = await session.get(AgentRun, uuid.UUID(str(run_id)))
        if run is None or not (run.budgets or {}).get("project_task_id"):
            return "no_project_task"
        task = await session.get(
            ProjectTask, uuid.UUID(run.budgets["project_task_id"])
        )
        if task is None or task.run_id != run.id:
            return "no_project_task"
        project = await session.get(Project, task.project_id)

        if run.status in ACTIVE_RUN_STATUSES and run.status != RunStatus.WAITING_APPROVAL:
            return "run_still_active"

        outcome = "unchanged"
        if run.status == RunStatus.WAITING_APPROVAL:
            if task.status == PMTaskStatus.RUNNING:
                transition(task, PMTaskStatus.WAITING_APPROVAL)
                await recorder.emit(
                    session,
                    project_id=project.id,
                    organization_id=project.organization_id,
                    type="task.waiting_approval",
                    payload={"ordinal": task.ordinal, "run_id": str(run.id)},
                )
            outcome = "waiting_approval"
        elif run.status == RunStatus.COMPLETED:
            if task.status == PMTaskStatus.WAITING_APPROVAL:
                transition(task, PMTaskStatus.RUNNING)  # approval granted; resumed
            if task.status != PMTaskStatus.RUNNING:
                return "already_finalized"
            output = await acceptance.first_completed_output(session, run.id)
            task.result = acceptance.build_result(task, run, output)
            transition(task, PMTaskStatus.REVIEWING)
            review = await acceptance.evaluate_acceptance(session, task, run, task.result)
            if review.passed:
                transition(task, PMTaskStatus.SUCCEEDED)
                task.error = None
                await recorder.emit(
                    session,
                    project_id=project.id,
                    organization_id=project.organization_id,
                    type="task.succeeded",
                    payload={"ordinal": task.ordinal, "attempt": task.attempts},
                )
                outcome = "succeeded"
            else:
                outcome = await _handle_rejected_attempt(
                    session, recorder, project, task, review.issues
                )
        elif run.status == RunStatus.FAILED:
            outcome = await _handle_failed_attempt(
                session, recorder, project, task, run.error or "run failed"
            )
        elif run.status == RunStatus.CANCELLED:
            if task.status not in (PMTaskStatus.CANCELLED, PMTaskStatus.SUCCEEDED):
                if task.status == PMTaskStatus.WAITING_APPROVAL:
                    transition(task, PMTaskStatus.FAILED)
                    task.error = "underlying run was cancelled"
                else:
                    transition(task, PMTaskStatus.CANCELLED)
                await recorder.emit(
                    session,
                    project_id=project.id,
                    organization_id=project.organization_id,
                    type="task.cancelled",
                    payload={"ordinal": task.ordinal},
                )
            outcome = "cancelled"

        await _maybe_finish_project(session, recorder, project)
        await session.commit()
        await recorder.publish_pending()
        project_id = str(project.id)

    if outcome in ("succeeded", "failed", "rework", "cancelled"):
        await schedule_project(project_id)  # cascade: unblock dependents / next slot
    return outcome


async def _handle_rejected_attempt(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    project: Project,
    task: ProjectTask,
    issues: list,
) -> str:
    feedback = "; ".join(str(issue) for issue in issues[:5]) or "acceptance criteria not met"
    transition(task, PMTaskStatus.NEEDS_REVISION)
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.review_failed",
        payload={"ordinal": task.ordinal, "attempt": task.attempts, "issues": issues[:5]},
    )
    if task.attempts < task.max_attempts:
        task.revision_feedback = feedback
        transition(task, PMTaskStatus.READY)
        await recorder.emit(
            session,
            project_id=project.id,
            organization_id=project.organization_id,
            type="task.revision_created",
            payload={
                "ordinal": task.ordinal,
                "next_attempt": task.attempts + 1,
                "feedback": feedback,
            },
        )
        return "rework"
    transition(task, PMTaskStatus.FAILED)
    task.error = f"acceptance failed after {task.attempts} attempt(s): {feedback}"
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.failed",
        payload={"ordinal": task.ordinal, "error": task.error},
    )
    return "failed"


async def _handle_failed_attempt(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    project: Project,
    task: ProjectTask,
    error: str,
) -> str:
    if task.status == PMTaskStatus.WAITING_APPROVAL:
        transition(task, PMTaskStatus.FAILED)
    elif task.status == PMTaskStatus.RUNNING:
        if task.attempts < task.max_attempts:
            transition(task, PMTaskStatus.READY)
            task.error = error
            await recorder.emit(
                session,
                project_id=project.id,
                organization_id=project.organization_id,
                type="task.retrying",
                payload={"ordinal": task.ordinal, "error": error, "attempt": task.attempts},
            )
            return "rework"
        transition(task, PMTaskStatus.FAILED)
    else:
        return "unchanged"
    task.error = error
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.failed",
        payload={"ordinal": task.ordinal, "error": error},
    )
    return "failed"


async def _maybe_finish_project(
    session: AsyncSession, recorder: ProjectEventRecorder, project: Project
) -> None:
    tasks = await project_tasks(session, project.id)
    if not tasks or project.status != ProjectStatus.ACTIVE.value:
        return
    if all(t.status == PMTaskStatus.SUCCEEDED for t in tasks):
        project.status = ProjectStatus.COMPLETED.value
        await recorder.emit(
            session,
            project_id=project.id,
            organization_id=project.organization_id,
            type="project.completed",
            payload={"tasks": len(tasks)},
        )
    elif any(t.status == PMTaskStatus.FAILED for t in tasks) and not any(
        t.status
        in (PMTaskStatus.READY, PMTaskStatus.WAITING_APPROVAL, *ACTIVE_TASK_STATUSES)
        for t in tasks
    ):
        # Failed work with nothing actionable left → surface, but keep project
        # active so the owner can retry; the UI highlights failed tasks.
        await recorder.emit(
            session,
            project_id=project.id,
            organization_id=project.organization_id,
            type="project.attention",
            payload={
                "failed": [t.ordinal for t in tasks if t.status == PMTaskStatus.FAILED]
            },
        )


async def recover_stale_tasks(ttl_seconds: int = STALE_CLAIM_TTL_S) -> list[str]:
    """Crash recovery: tasks claimed/running past the TTL whose worker never
    finalized them. Idempotent — a second sweep finds nothing to do."""
    sessionmaker = get_sessionmaker()
    recorder = ProjectEventRecorder()
    recovered: list[str] = []
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=ttl_seconds)
    async with sessionmaker() as session:
        stale = (
            await session.scalars(
                select(ProjectTask).where(
                    ProjectTask.status.in_(
                        [PMTaskStatus.ASSIGNED, PMTaskStatus.RUNNING]
                    ),
                    ProjectTask.claimed_at.isnot(None),
                    ProjectTask.claimed_at < cutoff,
                )
            )
        ).all()
        for task in stale:
            project = await session.get(Project, task.project_id)
            # Mark the orphan run failed so its history is truthful.
            if task.run_id is not None:
                run = await session.get(AgentRun, task.run_id)
                if run is not None and run.status in ACTIVE_RUN_STATUSES:
                    run.status = RunStatus.FAILED
                    run.error = "worker lost (recovered by stale-claim sweep)"
                    run.finished_at = dt.datetime.now(dt.UTC)
            if task.attempts < task.max_attempts:
                transition(task, PMTaskStatus.READY)
                task.error = "worker lost; attempt will be retried"
                event = "task.recovered"
            else:
                transition(task, PMTaskStatus.FAILED)
                task.error = "worker lost and retry limit reached"
                event = "task.failed"
            await recorder.emit(
                session,
                project_id=project.id,
                organization_id=project.organization_id,
                type=event,
                payload={"ordinal": task.ordinal, "attempts": task.attempts},
            )
            recovered.append(str(task.id))
        await session.commit()
        await recorder.publish_pending()
    return recovered
