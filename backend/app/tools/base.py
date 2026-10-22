"""Tool system core types.

Every tool declares typed input/output schemas, a required permission, timeout,
approval and idempotency flags, and which argument fields must be redacted from
logs/events. Execution happens only through the registry (app/tools/registry.py).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class ToolError(Exception):
    """Base error. Message must be safe to show to users."""


class ToolInputError(ToolError):
    """Invalid arguments (never retried)."""


class ToolPermissionError(ToolError):
    """Caller may not use this tool."""


class ToolTransientError(ToolError):
    """Transient failure — safe to retry for idempotent tools."""


class ToolRejectedError(ToolError):
    """A human rejected the operation."""


class ApprovalPendingError(ToolError):
    """Execution paused until a human decides. Carries the approval request id."""

    def __init__(self, approval_request_id: uuid.UUID, message: str = "approval required"):
        super().__init__(message)
        self.approval_request_id = approval_request_id


@dataclass
class ToolContext:
    """Everything a tool implementation may touch. No raw request, no secrets."""

    session: AsyncSession
    organization_id: uuid.UUID
    run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    agent_key: str
    tool_call_id: uuid.UUID | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[BaseModel]]
    required_permission: str = "tools.execute"
    requires_approval: bool = False
    timeout_s: float = 30.0
    idempotent: bool = True
    redact_fields: tuple[str, ...] = ()
    source: str = "builtin"  # builtin | mcp:<server>

    def redacted_args(self, args: dict) -> dict:
        from app.core.logging import REDACTED

        return {k: (REDACTED if k in self.redact_fields else v) for k, v in args.items()}


@dataclass
class ToolExecution:
    """Result of a registry execution, with audit metadata."""

    tool_call_id: uuid.UUID
    output: BaseModel
    duration_ms: int
    summary: dict = field(default_factory=dict)
