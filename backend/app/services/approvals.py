"""Approval decisions — the server-side gate for high-impact operations.

Only admins/owners of the owning organization may decide. The decision is a DB
state transition recorded in the audit log; the engine re-checks the row before
executing the gated tool. UI dialogs have no authority here.
"""

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.recorder import EventRecorder
from app.models import (
    ActorType,
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    MemberRole,
    RunStatus,
)
from app.services.audit import record_audit


class ApprovalError(Exception):
    """Safe, user-facing message."""


DECIDER_ROLES = (MemberRole.ADMIN, MemberRole.OWNER)


async def decide_approval(
    session: AsyncSession,
    recorder: EventRecorder,
    *,
    approval: ApprovalRequest,
    decision: str,
    user_id: uuid.UUID,
    role: MemberRole,
    reason: str | None = None,
) -> ApprovalRequest:
    if role not in DECIDER_ROLES:
        raise ApprovalError("only organization admins or owners can decide approvals")
    if decision not in ("approve", "reject"):
        raise ApprovalError("decision must be 'approve' or 'reject'")
    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalError(f"approval is already {approval.status.value}")
    now = dt.datetime.now(dt.UTC)
    if approval.expires_at <= now:
        approval.status = ApprovalStatus.EXPIRED
        raise ApprovalError("approval request has expired")

    approval.status = (
        ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
    )
    approval.decided_by = user_id
    approval.decided_at = now
    approval.decision_reason = (reason or "")[:2000]

    await record_audit(
        session,
        action=f"approval.{decision}d",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=approval.organization_id,
        entity_type="approval_request",
        entity_id=str(approval.id),
        meta={"tool": approval.tool_name, "reason": approval.decision_reason},
    )
    await recorder.emit(
        session,
        run_id=approval.run_id,
        organization_id=approval.organization_id,
        type="approval.decided",
        payload={
            "approval_request_id": str(approval.id),
            "decision": approval.status.value,
            "tool": approval.tool_name,
            "decided_by": str(user_id),
            "reason": approval.decision_reason,
        },
    )
    return approval


async def resume_run_after_decision(session: AsyncSession, approval: ApprovalRequest) -> bool:
    """Re-enqueue the paused run so the engine acts on the decision.

    Call only after the decision has been committed: the worker may pick the task
    up immediately and must see the decided approval row. This function commits
    the status flip itself before enqueueing for the same reason.
    """
    run = await session.get(AgentRun, approval.run_id)
    if run is None or run.status != RunStatus.WAITING_APPROVAL:
        return False
    run.status = RunStatus.RUNNING
    await session.commit()
    from app.services.queueing import QueueUnavailableError, enqueue
    from app.worker.tasks import execute_run_task

    try:
        await enqueue(execute_run_task, str(run.id))
    except QueueUnavailableError as exc:
        # The decision IS recorded; the resume could not be scheduled. Record
        # an honest failure (retryable). A later retry creates a fresh run and
        # therefore a FRESH approval request for any gated tool — recovery can
        # never silently execute work that was not re-authorized.
        import datetime as dt

        from app.events.recorder import EventRecorder
        from app.services.project_scheduler import finalize_task_for_run

        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = dt.datetime.now(dt.UTC)
        recorder = EventRecorder()
        await recorder.emit(
            session,
            run_id=run.id,
            organization_id=run.organization_id,
            type="run.failed",
            payload={"error": run.error},
        )
        await session.commit()
        await recorder.publish_pending()
        await finalize_task_for_run(run.id)  # maps the project task honestly
        return False
    return True
