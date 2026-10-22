"""PM harness entities: plan versions, project tasks, dependencies, reviews, events.

Execution of a task attempt reuses the existing run engine (project_tasks.run_id →
agent_runs). These tables are the PM-level control plane above it.
"""

import datetime as dt
import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.runs import _enum


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class PMTaskStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    BLOCKED = "blocked"
    READY = "ready"
    WAITING_APPROVAL = "waiting_approval"
    ASSIGNED = "assigned"
    RUNNING = "running"
    REVIEWING = "reviewing"
    NEEDS_REVISION = "needs_revision"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Statuses from which no further automatic progress happens.
TERMINAL_TASK_STATUSES = {
    PMTaskStatus.SUCCEEDED,
    PMTaskStatus.CANCELLED,
}
ACTIVE_TASK_STATUSES = {
    PMTaskStatus.ASSIGNED,
    PMTaskStatus.RUNNING,
    PMTaskStatus.REVIEWING,
}


class ProjectPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Versioned plan proposals. Approving a plan registers/updates its tasks."""

    __tablename__ = "project_plans"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_plan_project_version"),
        Index("ix_plans_project", "project_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(
        _enum(PlanStatus), nullable=False, default=PlanStatus.PROPOSED
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assumptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    deliverables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Serialized task specs + dependency edges as proposed (audit trail; approval
    # materializes them into project_tasks / task_dependencies).
    tasks_spec: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_pm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "project_tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "ordinal", name="uq_project_task_ordinal"),
        Index("ix_project_tasks_project_status", "project_id", "status"),
        Index("ix_project_tasks_org", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)  # human reference: #1, #2…
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[PMTaskStatus] = mapped_column(
        _enum(PMTaskStatus), nullable=False, default=PMTaskStatus.DRAFT
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # lower = first
    # Assignment: agent_key is the effective assignee; requested_agent_key preserves
    # the owner's explicit intent and is never silently substituted.
    agent_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    requested_agent_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    input_requirements: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expected_deliverables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Concurrency-safe claim: set atomically by the scheduler (status ready→assigned).
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dependencies: Mapped[list["TaskDependency"]] = relationship(
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskDependency(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
        Index("ix_task_deps_task", "task_id"),
        Index("ix_task_deps_target", "depends_on_task_id"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[ProjectTask] = relationship(
        foreign_keys=[task_id], back_populates="dependencies"
    )


class TaskReview(Base, UUIDPrimaryKeyMixin):
    """Acceptance evaluation per attempt — deterministic checks + optional critique."""

    __tablename__ = "task_reviews"
    __table_args__ = (Index("ix_task_reviews_task", "task_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    criteria_results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reviewer: Mapped[str] = mapped_column(String(60), nullable=False, default="pm-deterministic")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only project timeline (mirrors execution_events for the PM layer)."""

    __tablename__ = "project_events"
    __table_args__ = (
        UniqueConstraint("project_id", "seq", name="uq_project_event_seq"),
        Index("ix_project_events_project_seq", "project_id", "seq"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
