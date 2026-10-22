"""Agent definitions, runs, tasks, events, tool calls, approvals, conversations, reports."""

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
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
from app.models.enums import (
    ApprovalStatus,
    ExecutionMode,
    RunStatus,
    TaskStatus,
    ToolCallStatus,
)


def _enum(enum_cls, length: int = 24):
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


class AgentDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Registry of agents. org_id NULL means a built-in system agent visible to all orgs."""

    __tablename__ = "agent_definitions"
    __table_args__ = (UniqueConstraint("key", "organization_id", name="uq_agent_key_org"),)

    key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_tools: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_runs_org_created", "organization_id", "created_at"),
        Index("ix_runs_status", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus), nullable=False, default=RunStatus.QUEUED
    )
    mode: Mapped[ExecutionMode] = mapped_column(
        _enum(ExecutionMode), nullable=False, default=ExecutionMode.DETERMINISTIC
    )
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budgets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Nullable = "not reported by provider", never fabricated.
    tokens_input: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tasks: Mapped[list["RunTask"]] = relationship(
        back_populates="run", order_by="RunTask.ordinal", cascade="all, delete-orphan"
    )


class RunTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "run_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_run_task_ordinal"),
        Index("ix_run_tasks_run", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_key: Mapped[str] = mapped_column(String(60), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    depends_on: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # ordinals
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus), nullable=False, default=TaskStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[ExecutionMode] = mapped_column(
        _enum(ExecutionMode), nullable=False, default=ExecutionMode.DETERMINISTIC
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="tasks")


class ExecutionEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only run timeline. seq is per-run monotonic; SSE event ids are seq values."""

    __tablename__ = "execution_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_event_run_seq"),
        Index("ix_events_run_seq", "run_id", "seq"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolCall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run", "run_id"),)

    # Nullable: tools may execute outside a run context (ad-hoc/system calls);
    # the audit log still records every execution.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_tasks.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_key: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # redacted
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ToolCallStatus] = mapped_column(
        _enum(ToolCallStatus), nullable=False, default=ToolCallStatus.RUNNING
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approvals_org_status", "organization_id", "status"),
        Index("ix_approvals_run", "run_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_tasks.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    operation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING
    )
    requested_by_agent: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_org", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    # PM conversation scope: one conversation per project (see services/pm.py).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class ConversationMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversation_messages"
    __table_args__ = (Index("ix_conv_messages_conv", "conversation_id"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured context for PM messages: parsed intent, proposed/executed actions.
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Report(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_run", "run_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="markdown")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    artifacts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
