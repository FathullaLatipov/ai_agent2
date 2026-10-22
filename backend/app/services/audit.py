"""Audit log writer. Call inside the same transaction as the action being audited."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActorType, AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_type: ActorType = ActorType.SYSTEM,
    actor_id: str | None = None,
    organization_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    meta: dict | None = None,
    ip_address: str | None = None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta or {},
            ip_address=ip_address,
        )
    )
