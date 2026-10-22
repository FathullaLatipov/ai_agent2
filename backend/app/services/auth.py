"""Registration and login. Business logic only — no HTTP concerns."""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models import ActorType, MemberRole, Organization, OrganizationMember, User
from app.services.audit import record_audit


class AuthError(Exception):
    """Raised with a safe, user-facing message."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "org"


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str,
    ip_address: str | None = None,
) -> tuple[User, Organization]:
    email = email.lower().strip()
    existing = await session.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        raise AuthError("an account with this email already exists")

    base_slug = _slugify(organization_name)
    slug = base_slug
    suffix = 1
    while await session.scalar(select(Organization).where(Organization.slug == slug)):
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    org = Organization(name=organization_name, slug=slug)
    session.add_all([user, org])
    await session.flush()
    session.add(
        OrganizationMember(organization_id=org.id, user_id=user.id, role=MemberRole.OWNER)
    )
    await record_audit(
        session,
        action="auth.register",
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        organization_id=org.id,
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip_address,
    )
    return user, org


async def authenticate_user(
    session: AsyncSession, *, email: str, password: str, ip_address: str | None = None
) -> User:
    email = email.lower().strip()
    user = await session.scalar(select(User).where(func.lower(User.email) == email))
    # Same generic error whether the user exists or not (no enumeration).
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")
    await record_audit(
        session,
        action="auth.login",
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip_address,
    )
    return user
