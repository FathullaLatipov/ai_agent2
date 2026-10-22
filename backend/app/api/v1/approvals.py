"""Approval endpoints. Deciding requires admin/owner of the owning organization."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import OrgCtx, SessionDep, require_role
from app.events.recorder import EventRecorder
from app.models import ApprovalRequest, ApprovalStatus, MemberRole
from app.schemas.runs import ApprovalOut, DecideApprovalRequest
from app.services.approvals import ApprovalError, decide_approval, resume_run_after_decision

router = APIRouter(prefix="/approvals", tags=["approvals"])

_admin = require_role(MemberRole.ADMIN)


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    session: SessionDep,
    ctx: OrgCtx,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    query = select(ApprovalRequest).where(
        ApprovalRequest.organization_id == ctx.organization.id
    )
    if status_filter:
        try:
            query = query.where(ApprovalRequest.status == ApprovalStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown status") from exc
    rows = (await session.scalars(query.order_by(ApprovalRequest.created_at.desc()))).all()
    return [ApprovalOut.model_validate(a) for a in rows]


@router.post("/{approval_id}/decide", response_model=ApprovalOut)
async def decide(
    approval_id: uuid.UUID,
    body: DecideApprovalRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_admin)],
):
    approval = await session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.organization_id == ctx.organization.id,  # tenant scope
        )
    )
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval request not found")
    recorder = EventRecorder()
    try:
        approval = await decide_approval(
            session,
            recorder,
            approval=approval,
            decision=body.decision,
            user_id=ctx.user.id,
            role=ctx.role,
            reason=body.reason,
        )
    except ApprovalError as exc:
        await session.commit()  # persists expiry transitions
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    # Commit the decision BEFORE resuming: the worker may pick the run up
    # immediately and must observe the decided row.
    await session.commit()
    await recorder.publish_pending()
    await resume_run_after_decision(session, approval)
    return ApprovalOut.model_validate(approval)
