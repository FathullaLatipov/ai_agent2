"""Team management endpoints.

Admin-scoped: list members, invite, change role, remove.
Public (pre-auth): inspect and accept an invite by its one-time token.
"""

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select

from app.core.deps import OrgCtx, SessionDep, rate_limit, require_role
from app.core.security import create_access_token
from app.models import MemberRole, OrganizationInvite, OrganizationMember, User
from app.schemas.auth import TokenResponse
from app.services.members import (
    MemberError,
    accept_invite,
    change_member_role,
    create_invite,
    get_invite_by_token,
    invite_is_valid,
    remove_member,
)

router = APIRouter(tags=["members"])

_admin = require_role(MemberRole.ADMIN)
_auth_limit = rate_limit("auth")

# Roles that can be granted through invites / role changes. OWNER is excluded
# by design; the service layer additionally enforces the actor's hierarchy.
GrantableRole = Literal["admin", "member", "viewer"]


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID  # membership id
    user_id: uuid.UUID
    email: str
    full_name: str
    role: MemberRole
    is_active: bool
    created_at: dt.datetime


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: MemberRole
    expires_at: dt.datetime
    created_at: dt.datetime


class InviteCreated(InviteOut):
    token: str  # raw token — returned exactly once


class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: GrantableRole = "member"


class RoleChangeRequest(BaseModel):
    role: GrantableRole


class InvitePublicOut(BaseModel):
    organization_name: str
    email: str
    role: MemberRole
    valid: bool
    reason: str | None


class InviteAcceptRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=128)


@router.get("/org/members", response_model=list[MemberOut])
async def list_members(session: SessionDep, ctx: Annotated[OrgCtx, Depends(_admin)]):
    rows = (
        await session.execute(
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == ctx.organization.id)
            .order_by(OrganizationMember.created_at)
        )
    ).all()
    return [
        MemberOut(
            id=m.id,
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=m.role,
            is_active=u.is_active,
            created_at=m.created_at,
        )
        for m, u in rows
    ]


@router.get("/org/invites", response_model=list[InviteOut])
async def list_invites(session: SessionDep, ctx: Annotated[OrgCtx, Depends(_admin)]):
    invites = (
        await session.scalars(
            select(OrganizationInvite)
            .where(
                OrganizationInvite.organization_id == ctx.organization.id,
                OrganizationInvite.accepted_at.is_(None),
                OrganizationInvite.revoked_at.is_(None),
                OrganizationInvite.expires_at > dt.datetime.now(dt.UTC),
            )
            .order_by(OrganizationInvite.created_at.desc())
        )
    ).all()
    return [InviteOut.model_validate(i) for i in invites]


@router.post("/org/invites", response_model=InviteCreated)
async def invite_member(
    body: InviteCreateRequest, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_admin)]
):
    try:
        invite, raw_token = await create_invite(
            session,
            organization_id=ctx.organization.id,
            email=body.email,
            role=MemberRole(body.role),
            actor_id=ctx.user.id,
            actor_role=ctx.role,
        )
    except MemberError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    return InviteCreated(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        created_at=invite.created_at,
        token=raw_token,
    )


@router.delete("/org/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_admin)]
):
    invite = await session.scalar(
        select(OrganizationInvite).where(
            OrganizationInvite.id == invite_id,
            OrganizationInvite.organization_id == ctx.organization.id,
        )
    )
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "invite already accepted")
    invite.revoked_at = dt.datetime.now(dt.UTC)
    await session.commit()


@router.patch("/org/members/{membership_id}", response_model=MemberOut)
async def update_member_role(
    membership_id: uuid.UUID,
    body: RoleChangeRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_admin)],
):
    try:
        member = await change_member_role(
            session,
            organization_id=ctx.organization.id,
            membership_id=membership_id,
            new_role=MemberRole(body.role),
            actor_id=ctx.user.id,
            actor_role=ctx.role,
        )
    except MemberError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    user = await session.get(User, member.user_id)
    return MemberOut(
        id=member.id,
        user_id=member.user_id,
        email=user.email if user else "",
        full_name=user.full_name if user else "",
        role=member.role,
        is_active=user.is_active if user else False,
        created_at=member.created_at,
    )


@router.delete("/org/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    membership_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_admin)]
):
    try:
        await remove_member(
            session,
            organization_id=ctx.organization.id,
            membership_id=membership_id,
            actor_id=ctx.user.id,
            actor_role=ctx.role,
        )
    except MemberError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()


# ---------- public invite endpoints (no auth) ----------


@router.get("/invites/{token}", response_model=InvitePublicOut)
async def inspect_invite(token: str, session: SessionDep):
    found = await get_invite_by_token(session, token)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    invite, org = found
    valid, reason = invite_is_valid(invite)
    return InvitePublicOut(
        organization_name=org.name,
        email=invite.email,
        role=invite.role,
        valid=valid,
        reason=reason,
    )


@router.post(
    "/invites/{token}/accept",
    response_model=TokenResponse,
    dependencies=[Depends(_auth_limit)],
)
async def accept(token: str, body: InviteAcceptRequest, session: SessionDep, request: Request):
    try:
        user = await accept_invite(
            session,
            raw_token=token,
            full_name=body.full_name,
            password=body.password,
            ip_address=request.client.host if request.client else None,
        )
    except MemberError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))
