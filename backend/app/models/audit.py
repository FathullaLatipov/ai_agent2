"""Append-only audit log."""

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ActorType
from app.models.runs import _enum


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType), nullable=False, default=ActorType.SYSTEM
    )
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
