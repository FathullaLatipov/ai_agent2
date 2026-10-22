"""Users, organizations, memberships, projects."""

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MemberRole


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list["OrganizationMember"]] = relationship(back_populates="user")


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)

    members: Mapped[list["OrganizationMember"]] = relationship(back_populates="organization")


class OrganizationMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
        Index("ix_org_members_user", "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(
            MemberRole,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=MemberRole.MEMBER,
    )

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_org", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # PM harness fields (see docs/pm-harness.md). status values: ProjectStatus.
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
