"""Run lifecycle service — create, cancel, retry. HTTP-agnostic."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.recorder import EventRecorder
from app.models import (
    ACTIVE_RUN_STATUSES,
    ActorType,
    AgentRun,
    ExecutionMode,
    RunStatus,
    RunTask,
    TaskStatus,
)
from app.services.audit import record_audit


class RunServiceError(Exception):
    """Safe, user-facing message."""


async def create_run(
    session: AsyncSession,
    recorder: EventRecorder,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    input_text: str,
    title: str | None = None,
    params: dict | None = None,
    project_id: uuid.UUID | None = None,
) -> AgentRun:
    from app.llm.factory import get_llm_provider

    mode = ExecutionMode.LLM if get_llm_provider() is not None else ExecutionMode.DETERMINISTIC
    run = AgentRun(
        organization_id=organization_id,
        project_id=project_id,
        created_by=user_id,
        title=(title or input_text[:120]).strip() or "Untitled run",
        input_text=input_text,
        status=RunStatus.QUEUED,
        mode=mode,
        budgets={"request_params": params or {}},
        correlation_id=uuid.uuid4().hex,
    )
    session.add(run)
    await session.flush()
    await recorder.emit(
        session,
        run_id=run.id,
        organization_id=organization_id,
        type="run.created",
        payload={"title": run.title, "mode": mode.value},
    )
    await record_audit(
        session,
        action="run.created",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=organization_id,
        entity_type="agent_run",
        entity_id=str(run.id),
    )
    return run


async def enqueue_run(run_id: uuid.UUID) -> None:
    from app.worker.tasks import execute_run_task

    await execute_run_task.kiq(str(run_id))


async def request_cancel(
    session: AsyncSession,
    recorder: EventRecorder,
    *,
    run: AgentRun,
    user_id: uuid.UUID,
) -> AgentRun:
    if run.status not in ACTIVE_RUN_STATUSES:
        raise RunServiceError(f"run is {run.status.value} and cannot be cancelled")
    run.cancel_requested = True
    previous = run.status
    if previous in (RunStatus.QUEUED, RunStatus.WAITING_APPROVAL):
        # Nothing is executing — finalize immediately.
        import datetime as dt

        run.status = RunStatus.CANCELLED
        run.finished_at = dt.datetime.now(dt.UTC)
        for task in (
            await session.scalars(select(RunTask).where(RunTask.run_id == run.id))
        ).all():
            if task.status in (
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_APPROVAL,
            ):
                task.status = TaskStatus.CANCELLED
        await recorder.emit(
            session,
            run_id=run.id,
            organization_id=run.organization_id,
            type="run.cancelled",
            payload={"by_user": str(user_id)},
        )
    else:
        run.status = RunStatus.CANCELLING
        await recorder.emit(
            session,
            run_id=run.id,
            organization_id=run.organization_id,
            type="run.cancel_requested",
            payload={"by_user": str(user_id)},
        )
    await record_audit(
        session,
        action="run.cancel_requested",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=run.organization_id,
        entity_type="agent_run",
        entity_id=str(run.id),
    )
    return run


async def retry_run(
    session: AsyncSession,
    recorder: EventRecorder,
    *,
    run: AgentRun,
    user_id: uuid.UUID,
) -> AgentRun:
    """Resume a failed run: failed tasks reset to pending, status back to queued."""
    if run.status != RunStatus.FAILED:
        raise RunServiceError(f"only failed runs can be retried (run is {run.status.value})")
    for task in (await session.scalars(select(RunTask).where(RunTask.run_id == run.id))).all():
        if task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.status = TaskStatus.PENDING
            task.error = None
            task.output = None
            task.attempts = 0
            task.finished_at = None
    run.status = RunStatus.QUEUED
    run.error = None
    run.finished_at = None
    run.cancel_requested = False
    await recorder.emit(
        session,
        run_id=run.id,
        organization_id=run.organization_id,
        type="run.retried",
        payload={"by_user": str(user_id)},
    )
    await record_audit(
        session,
        action="run.retried",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=run.organization_id,
        entity_type="agent_run",
        entity_id=str(run.id),
    )
    return run
