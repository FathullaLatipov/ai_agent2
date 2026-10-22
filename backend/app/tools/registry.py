"""The tool registry — the only path by which agents execute tools.

Responsibilities per call:
  1. Resolve the tool and validate arguments against its input schema.
  2. Enforce the calling agent's tool allowlist (server-side, from the DB-backed
     agent definition — never from model output).
  3. Enforce approval gates: a tool with requires_approval only runs when a
     matching approval_requests row is APPROVED (a human flipped it via the API).
  4. Enforce the per-call timeout.
  5. Persist the tool_calls row (arguments redacted per spec) and audit entry.
  6. Emit execution events.
"""

import asyncio
import datetime as dt
import hashlib
import json
import time

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.recorder import EventRecorder
from app.models import (
    ActorType,
    ApprovalRequest,
    ApprovalStatus,
    ToolCall,
    ToolCallStatus,
)
from app.services.audit import record_audit
from app.tools.base import (
    ApprovalPendingError,
    ToolContext,
    ToolError,
    ToolExecution,
    ToolInputError,
    ToolPermissionError,
    ToolRejectedError,
    ToolSpec,
    ToolTransientError,
)

logger = get_logger("agentos.tools")


def args_fingerprint(tool_name: str, args: dict) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()[:32]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name} already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda s: s.name)

    async def execute(
        self,
        ctx: ToolContext,
        *,
        tool_name: str,
        arguments: dict,
        allowed_tools: list[str] | None,
        recorder: EventRecorder | None = None,
    ) -> ToolExecution:
        """Execute a tool call with full enforcement. Raises ToolError subclasses."""
        spec = self._tools.get(tool_name)
        if spec is None:
            raise ToolInputError(f"unknown tool: {tool_name}")

        # Server-side allowlist enforcement (None = internal/system caller).
        if allowed_tools is not None and tool_name not in allowed_tools:
            raise ToolPermissionError(
                f"agent '{ctx.agent_key}' is not permitted to call tool '{tool_name}'"
            )

        try:
            parsed_input = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise ToolInputError(f"invalid arguments for {tool_name}: {exc}") from exc

        session = ctx.session
        redacted = spec.redacted_args(parsed_input.model_dump(mode="json"))

        approval: ApprovalRequest | None = None
        if spec.requires_approval:
            approval = await self._resolve_approval(ctx, spec, redacted, recorder)

        call = ToolCall(
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            organization_id=ctx.organization_id,
            tool_name=tool_name,
            agent_key=ctx.agent_key,
            arguments=redacted,
            status=ToolCallStatus.RUNNING,
            idempotency_key=args_fingerprint(tool_name, redacted),
            approval_request_id=approval.id if approval else None,
        )
        session.add(call)
        await session.flush()
        ctx.tool_call_id = call.id

        if recorder and ctx.run_id:
            await recorder.emit(
                session,
                run_id=ctx.run_id,
                organization_id=ctx.organization_id,
                type="tool.called",
                payload={
                    "tool_call_id": str(call.id),
                    "tool": tool_name,
                    "agent": ctx.agent_key,
                    "arguments": redacted,
                },
            )

        start = time.monotonic()
        try:
            async with asyncio.timeout(spec.timeout_s):
                output = await spec.handler(ctx, parsed_input)
        except TimeoutError as exc:
            await self._finish(
                ctx, call, ToolCallStatus.TIMED_OUT, error=f"timed out after {spec.timeout_s}s",
                recorder=recorder,
            )
            if not spec.idempotent:
                # The operation may have executed despite the timeout. Never
                # hand the engine a retryable error for non-idempotent tools.
                raise ToolError(
                    f"tool {tool_name} timed out and is not idempotent — "
                    "not retrying automatically; verify the external state"
                ) from exc
            raise ToolTransientError(f"tool {tool_name} timed out") from exc
        except ToolError as exc:
            await self._finish(ctx, call, ToolCallStatus.FAILED, error=str(exc), recorder=recorder)
            raise
        except Exception as exc:
            logger.exception("tool crashed", tool=tool_name)
            await self._finish(
                ctx, call, ToolCallStatus.FAILED, error="internal tool error", recorder=recorder
            )
            raise ToolError(f"tool {tool_name} failed internally") from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            validated = spec.output_model.model_validate(output)
        except ValidationError as exc:
            await self._finish(
                ctx, call, ToolCallStatus.FAILED, error="tool produced invalid output",
                recorder=recorder,
            )
            raise ToolError(f"tool {tool_name} produced invalid output") from exc

        summary = _summarize_output(validated)
        call.status = ToolCallStatus.COMPLETED
        call.duration_ms = duration_ms
        call.result_summary = summary
        call.finished_at = dt.datetime.now(dt.UTC)
        await record_audit(
            session,
            action="tool.executed",
            actor_type=ActorType.AGENT,
            actor_id=ctx.agent_key,
            organization_id=ctx.organization_id,
            entity_type="tool_call",
            entity_id=str(call.id),
            meta={"tool": tool_name, "duration_ms": duration_ms},
        )
        if recorder and ctx.run_id:
            await recorder.emit(
                session,
                run_id=ctx.run_id,
                organization_id=ctx.organization_id,
                type="tool.completed",
                payload={
                    "tool_call_id": str(call.id),
                    "tool": tool_name,
                    "duration_ms": duration_ms,
                    "summary": summary,
                },
            )
        return ToolExecution(
            tool_call_id=call.id, output=validated, duration_ms=duration_ms, summary=summary
        )

    async def _resolve_approval(
        self,
        ctx: ToolContext,
        spec: ToolSpec,
        redacted_args: dict,
        recorder: EventRecorder | None,
    ) -> ApprovalRequest:
        """Find or create the approval request for this exact operation.

        The gate is the DB row's status — set only by an authorized human through
        the approvals API. PENDING pauses execution; REJECTED refuses it.
        """
        session = ctx.session
        fingerprint = args_fingerprint(spec.name, redacted_args)
        existing = await session.scalar(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.run_id == ctx.run_id,
                ApprovalRequest.tool_name == spec.name,
                ApprovalRequest.organization_id == ctx.organization_id,
            )
            .order_by(ApprovalRequest.created_at.desc())
        )
        if existing is not None and existing.payload.get("fingerprint") == fingerprint:
            if existing.status == ApprovalStatus.APPROVED:
                return existing
            if existing.status == ApprovalStatus.REJECTED:
                raise ToolRejectedError(
                    f"operation '{spec.name}' was rejected by a reviewer"
                )
            if existing.status == ApprovalStatus.EXPIRED:
                raise ToolRejectedError(f"approval for '{spec.name}' expired before a decision")
            raise ApprovalPendingError(existing.id)

        settings = get_settings()
        request = ApprovalRequest(
            organization_id=ctx.organization_id,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            tool_name=spec.name,
            operation_summary=f"{spec.description}",
            payload={"arguments": redacted_args, "fingerprint": fingerprint},
            status=ApprovalStatus.PENDING,
            requested_by_agent=ctx.agent_key,
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=settings.approval_ttl_hours),
        )
        session.add(request)
        await session.flush()
        await record_audit(
            session,
            action="approval.requested",
            actor_type=ActorType.AGENT,
            actor_id=ctx.agent_key,
            organization_id=ctx.organization_id,
            entity_type="approval_request",
            entity_id=str(request.id),
            meta={"tool": spec.name},
        )
        if recorder and ctx.run_id:
            await recorder.emit(
                session,
                run_id=ctx.run_id,
                organization_id=ctx.organization_id,
                type="approval.requested",
                payload={
                    "approval_request_id": str(request.id),
                    "tool": spec.name,
                    "summary": request.operation_summary,
                    "arguments": redacted_args,
                },
            )
        raise ApprovalPendingError(request.id)

    async def _finish(
        self,
        ctx: ToolContext,
        call: ToolCall,
        status: ToolCallStatus,
        *,
        error: str,
        recorder: EventRecorder | None,
    ) -> None:
        call.status = status
        call.error = error
        call.finished_at = dt.datetime.now(dt.UTC)
        if recorder and ctx.run_id:
            await recorder.emit(
                ctx.session,
                run_id=ctx.run_id,
                organization_id=ctx.organization_id,
                type="tool.failed",
                payload={
                    "tool_call_id": str(call.id),
                    "tool": call.tool_name,
                    "status": status.value,
                    "error": error,
                },
            )


def _summarize_output(output: BaseModel) -> dict:
    """Compact, safe summary persisted with the tool call (full outputs stay in task state)."""
    data = output.model_dump(mode="json")
    text = json.dumps(data, default=str)
    if len(text) > 4000:
        return {"truncated": True, "preview": text[:4000]}
    return data


# Global registry, populated by app.tools.builtin at import time and by the MCP
# adapter at startup/discovery.
registry = ToolRegistry()
