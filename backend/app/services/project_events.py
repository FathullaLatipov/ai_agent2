"""Project-level event recording + pub/sub fan-out (mirrors app/events/recorder.py)."""

import datetime as dt
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models import ProjectEvent

logger = get_logger("agentos.project_events")


def channel_for_project(project_id: uuid.UUID | str) -> str:
    return f"agentos:project-events:{project_id}"


class ProjectEventRecorder:
    def __init__(self) -> None:
        self._pending: list[tuple[str, dict]] = []

    async def emit(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        organization_id: uuid.UUID,
        type: str,
        payload: dict | None = None,
    ) -> ProjectEvent:
        payload = payload or {}
        last_error: Exception | None = None
        for _attempt in range(3):
            seq = (
                await session.scalar(
                    select(func.coalesce(func.max(ProjectEvent.seq), 0)).where(
                        ProjectEvent.project_id == project_id
                    )
                )
            ) + 1
            event = ProjectEvent(
                project_id=project_id,
                organization_id=organization_id,
                seq=seq,
                type=type,
                payload=payload,
            )
            session.add(event)
            try:
                await session.flush()
            except IntegrityError as exc:
                last_error = exc
                await session.rollback()
                continue
            self._pending.append(
                (
                    channel_for_project(project_id),
                    {
                        "id": seq,
                        "type": type,
                        "payload": payload,
                        "project_id": str(project_id),
                        "created_at": dt.datetime.now(dt.UTC).isoformat(),
                    },
                )
            )
            return event
        raise RuntimeError("could not allocate project event sequence") from last_error

    async def publish_pending(self) -> None:
        pending, self._pending = self._pending, []
        if not pending:
            return
        try:
            redis = get_redis()
            for channel, message in pending:
                await redis.publish(channel, json.dumps(message, default=str))
        except Exception:
            logger.warning("project event publish failed; SSE replay covers", count=len(pending))
