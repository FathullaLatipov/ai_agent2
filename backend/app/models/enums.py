"""Status enums. Stored as varchar + CHECK constraints (native_enum=False)."""

from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    REVIEWING = "reviewing"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


# Statuses from which a run can still make progress.
ACTIVE_RUN_STATUSES = {
    RunStatus.QUEUED,
    RunStatus.PLANNING,
    RunStatus.RUNNING,
    RunStatus.WAITING_APPROVAL,
    RunStatus.REVIEWING,
    RunStatus.REPORTING,
    RunStatus.CANCELLING,
}


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ToolCallStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ExecutionMode(StrEnum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
