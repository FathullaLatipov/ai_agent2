"""Team management: invites, role changes, member removal.

Business logic only — no HTTP concerns. Rules:
- Invites are one-time links with a fixed role; only the SHA-256 of the token
  is stored, the raw token is returned once at creation.
- Role hierarchy is enforced: an actor may only manage members/invites whose
  role is strictly below their own, and may only grant roles below their own.
  OWNER cannot be granted or removed through this module at all.
"""

import datetime as dt
import hashlib
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    ActorType,
    MemberRole,
    Organization,
    OrganizationInvite,
    OrganizationMember,
    User,
)
from app.services.audit import record_audit

INVITE_TTL = dt.timedelta(days=7)

_ROLE_ORDER = {
    MemberRole.VIEWER: 0,
    MemberRole.MEMBER: 1,
    MemberRole.ADMIN: 2,
    MemberRole.OWNER: 3,
}


class MemberError(Exception):
    """Raised with a safe, user-facing message."""


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _require_outranks(actor_role: MemberRole, target_role: MemberRole, action: str) -> None:
    if target_role == MemberRole.OWNER:
        raise MemberError(f"the owner cannot be {action}")
    if _ROLE_ORDER[actor_role] <= _ROLE_ORDER[target_role]:
        raise MemberError(f"your role cannot {action} a {target_role.value}")


async def create_invite(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role: MemberRole,
    actor_id: uuid.UUID,
    actor_role: MemberRole,
) -> tuple[OrganizationInvite, str]:
    """Create a one-time invite; returns (invite, raw_token)."""
    email = email.lower().strip()
    if role == MemberRole.OWNER or _ROLE_ORDER[actor_role] <= _ROLE_ORDER[role]:
        raise MemberError("you can only grant roles below your own")

    existing_user = await session.scalar(
        select(User).where(func.lower(User.email) == email)
    )
    if existing_user is not None:
        member = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == existing_user.id,
            )
        )
        if member is not None:
            raise MemberError("this email already belongs to a member of the workspace")
        raise MemberError(
            "an account with this email already exists in another workspace — "
            "ask them to use a different email"
        )

    # Re-inviting the same email supersedes the previous pending link.
    pending = (
        await session.scalars(
            select(OrganizationInvite).where(
                OrganizationInvite.organization_id == organization_id,
                OrganizationInvite.email == email,
                OrganizationInvite.accepted_at.is_(None),
                OrganizationInvite.revoked_at.is_(None),
            )
        )
    ).all()
    for old in pending:
        old.revoked_at = _now()

    raw_token = secrets.token_urlsafe(32)
    invite = OrganizationInvite(
        organization_id=organization_id,
        email=email,
        role=role,
        token_hash=_hash_token(raw_token),
        invited_by=actor_id,
        expires_at=_now() + INVITE_TTL,
    )
    session.add(invite)
    await session.flush()
    await record_audit(
        session,
        action="member.invited",
        actor_type=ActorType.USER,
        actor_id=str(actor_id),
        organization_id=organization_id,
        entity_type="invite",
        entity_id=str(invite.id),
        meta={"email": email, "role": role.value},
    )
    return invite, raw_token


def invite_is_valid(invite: OrganizationInvite) -> tuple[bool, str | None]:
    if invite.accepted_at is not None:
        return False, "this invite has already been used"
    if invite.revoked_at is not None:
        return False, "this invite was revoked"
    if invite.expires_at <= _now():
        return False, "this invite has expired"
    return True, None


async def get_invite_by_token(
    session: AsyncSession, raw_token: str
) -> tuple[OrganizationInvite, Organization] | None:
    invite = await session.scalar(
        select(OrganizationInvite).where(
            OrganizationInvite.token_hash == _hash_token(raw_token)
        )
    )
    if invite is None:
        return None
    org = await session.get(Organization, invite.organization_id)
    if org is None:  # pragma: no cover - FK guarantees existence
        return None
    return invite, org


async def accept_invite(
    session: AsyncSession,
    *,
    raw_token: str,
    full_name: str,
    password: str,
    ip_address: str | None = None,
) -> User:
    found = await get_invite_by_token(session, raw_token)
    if found is None:
        raise MemberError("invite not found")
    invite, _org = found
    valid, reason = invite_is_valid(invite)
    if not valid:
        raise MemberError(reason or "invite is no longer valid")

    existing = await session.scalar(
        select(User).where(func.lower(User.email) == invite.email)
    )
    if existing is not None:
        raise MemberError(
            "an account with this email already exists — contact your workspace admin"
        )

    user = User(
        email=invite.email,
        password_hash=hash_password(password),
        full_name=full_name,
    )
    session.add(user)
    await session.flush()
    session.add(
        OrganizationMember(
            organization_id=invite.organization_id, user_id=user.id, role=invite.role
        )
    )
    invite.accepted_at = _now()
    await record_audit(
        session,
        action="member.joined",
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        organization_id=invite.organization_id,
        entity_type="invite",
        entity_id=str(invite.id),
        meta={"email": invite.email, "role": invite.role.value},
        ip_address=ip_address,
    )
    return user


async def change_member_role(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    new_role: MemberRole,
    actor_id: uuid.UUID,
    actor_role: MemberRole,
) -> OrganizationMember:
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.id == membership_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    if member is None:
        raise MemberError("member not found")
    if member.user_id == actor_id:
        raise MemberError("you cannot change your own role")
    _require_outranks(actor_role, member.role, "modified")
    if new_role == MemberRole.OWNER or _ROLE_ORDER[actor_role] <= _ROLE_ORDER[new_role]:
        raise MemberError("you can only grant roles below your own")

    old_role = member.role
    member.role = new_role
    await record_audit(
        session,
        action="member.role_changed",
        actor_type=ActorType.USER,
        actor_id=str(actor_id),
        organization_id=organization_id,
        entity_type="membership",
        entity_id=str(member.id),
        meta={"from": old_role.value, "to": new_role.value},
    )
    return member


async def remove_member(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_role: MemberRole,
) -> None:
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.id == membership_id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    if member is None:
        raise MemberError("member not found")
    if member.user_id == actor_id:
        raise MemberError("you cannot remove yourself")
    _require_outranks(actor_role, member.role, "removed")

    user_id = member.user_id
    await session.delete(member)
    await session.flush()
    # Lock the account out entirely when this was their only workspace.
    remaining = await session.scalar(
        select(func.count(OrganizationMember.id)).where(
            OrganizationMember.user_id == user_id
        )
    )
    if not remaining:
        user = await session.get(User, user_id)
        if user is not None:
            user.is_active = False
    await record_audit(
        session,
        action="member.removed",
        actor_type=ActorType.USER,
        actor_id=str(actor_id),
        organization_id=organization_id,
        entity_type="membership",
        entity_id=str(membership_id),
        meta={"user_id": str(user_id)},
    )
