"""Execution event recording and fan-out.

Every event is persisted (source of truth, replayable) and then published on
Redis pub/sub for live SSE delivery. Publishing happens only after the DB commit:
callers emit events inside their transaction, commit, then `await publish_pending()`.
If Redis publishing fails, events remain in the DB and reach clients on reconnect.
"""

import datetime as dt
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models import ExecutionEvent

logger = get_logger("agentos.events")


def channel_for_run(run_id: uuid.UUID | str) -> str:
    return f"agentos:events:{run_id}"


class EventRecorder:
    """Per-execution event writer. Not thread-safe; use one per logical flow."""

    def __init__(self) -> None:
        self._pending: list[tuple[str, dict]] = []

    async def emit(
        self,
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        organization_id: uuid.UUID,
        type: str,
        payload: dict | None = None,
    ) -> ExecutionEvent:
        payload = payload or {}
        last_error: Exception | None = None
        for _attempt in range(3):
            seq = (
                await session.scalar(
                    select(func.coalesce(func.max(ExecutionEvent.seq), 0)).where(
                        ExecutionEvent.run_id == run_id
                    )
                )
            ) + 1
            event = ExecutionEvent(
                run_id=run_id, organization_id=organization_id, seq=seq, type=type, payload=payload
            )
            session.add(event)
            try:
                await session.flush()
            except IntegrityError as exc:  # concurrent writer took this seq
                last_error = exc
                await session.rollback()
                continue
            self._pending.append(
                (
                    channel_for_run(run_id),
                    {
                        "id": seq,
                        "type": type,
                        "payload": payload,
                        "run_id": str(run_id),
                        "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    },
                )
            )
            return event
        raise RuntimeError("could not allocate event sequence number") from last_error

    async def publish_pending(self) -> None:
        """Publish events emitted since the last call. Call after commit."""
        pending, self._pending = self._pending, []
        if not pending:
            return
        try:
            redis = get_redis()
            for channel, message in pending:
                await redis.publish(channel, json.dumps(message, default=str))
        except Exception:
            # Events are already durable in the DB; live listeners catch up on reconnect.
            logger.warning("event publish failed; relying on SSE replay", count=len(pending))
