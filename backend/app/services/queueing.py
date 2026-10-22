"""Safe task enqueueing.

A database commit followed by a failed Redis enqueue must never leave work
looking "accepted". Callers choose one of two modes:

- `enqueue(...)`  — raises QueueUnavailableError; the caller records an honest
  terminal/retryable state for the orphaned row and returns an actionable
  response (used where a specific record was just persisted).
- `try_enqueue(...)` — returns False and logs; only for idempotent,
  user-repeatable "kick the scheduler" signals where the persisted state is
  already accurate without the enqueue (tasks simply stay ready).
"""

from app.core.logging import get_logger

logger = get_logger("agentos.queue")

QUEUE_UNAVAILABLE_MSG = (
    "task queue unavailable — the operation was recorded but not scheduled; "
    "retry once the queue is reachable"
)


class QueueUnavailableError(Exception):
    """Redis/broker could not accept the task."""

    def __init__(self, message: str = QUEUE_UNAVAILABLE_MSG):
        super().__init__(message)


async def enqueue(task, *args) -> None:
    try:
        await task.kiq(*args)
    except Exception as exc:  # taskiq wraps broker errors in SendTaskError
        logger.warning("enqueue failed", task=getattr(task, "task_name", "?"), error=str(exc))
        raise QueueUnavailableError() from exc


async def try_enqueue(task, *args) -> bool:
    try:
        await task.kiq(*args)
        return True
    except Exception as exc:
        logger.warning(
            "enqueue failed (non-fatal; state remains schedulable)",
            task=getattr(task, "task_name", "?"),
            error=str(exc),
        )
        return False
