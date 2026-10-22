"""FastAPI dependencies: authenticated user, org context, roles, rate limiting.

Org membership and role are resolved from the database on every request —
never trusted from the JWT.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models import MemberRole, Organization, OrganizationMember, User

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_ROLE_ORDER = {
    MemberRole.VIEWER: 0,
    MemberRole.MEMBER: 1,
    MemberRole.ADMIN: 2,
    MemberRole.OWNER: 3,
}


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(authorization.split(" ", 1)[1], expected_purpose="access")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account unavailable")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass
class OrgContext:
    user: User
    organization: Organization
    role: MemberRole


async def get_org_context(
    session: SessionDep,
    user: CurrentUser,
    x_organization_id: Annotated[str | None, Header()] = None,
) -> OrgContext:
    """Resolve the caller's organization. Uses the X-Organization-Id header when the
    user belongs to several orgs; otherwise their single membership."""
    query = select(OrganizationMember).where(OrganizationMember.user_id == user.id)
    if x_organization_id:
        try:
            org_id = uuid.UUID(x_organization_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid organization id") from exc
        query = query.where(OrganizationMember.organization_id == org_id)
    memberships = (await session.scalars(query)).all()
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this organization")
    if len(memberships) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "multiple organizations — specify X-Organization-Id",
        )
    membership = memberships[0]
    org = await session.get(Organization, membership.organization_id)
    if org is None:  # pragma: no cover - FK guarantees existence
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this organization")
    return OrgContext(user=user, organization=org, role=membership.role)


OrgCtx = Annotated[OrgContext, Depends(get_org_context)]


def require_role(minimum: MemberRole):
    async def dependency(ctx: OrgCtx) -> OrgContext:
        if _ROLE_ORDER[ctx.role] < _ROLE_ORDER[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return ctx

    return dependency


def rate_limit(scope: str, per_minute: int | None = None):
    """Fixed-window rate limit keyed on user id (or client IP before auth)."""

    async def dependency(request: Request) -> None:
        settings = get_settings()
        if settings.env == "test":
            return
        if per_minute is not None:
            limit = per_minute
        elif scope == "auth":
            limit = settings.rate_limit_auth_per_minute
        else:
            limit = settings.rate_limit_default_per_minute
        who = request.headers.get("authorization", "")[-24:] or (
            request.client.host if request.client else "unknown"
        )
        key = f"agentos:rl:{scope}:{who}"
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")

    return dependency
