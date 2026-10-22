"""Background tasks. Retry policy notes:

- execute_run_task is safe to re-deliver: the runner re-derives state from the
  database, completed tasks are never re-executed, and approval-gated tools check
  the approval row before acting.
- ingest_document_task is idempotent per document (chunks are replaced wholesale).
"""

from taskiq import TaskiqEvents, TaskiqState

from app.core.logging import get_logger
from app.worker.broker import broker

logger = get_logger("agentos.worker")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def recover_on_startup(_state: TaskiqState) -> None:
    """Crash recovery: on worker start, sweep tasks whose previous worker died
    mid-attempt (stale claims). Idempotent; TTL guards live work elsewhere."""
    from app.services.project_scheduler import recover_stale_tasks

    recovered = await recover_stale_tasks()
    if recovered:
        logger.info("recovered stale project tasks on startup", count=len(recovered))


@broker.task(retry_on_error=False)
async def execute_run_task(run_id: str) -> str:
    from app.engine.runner import execute_run

    outcome = await execute_run(run_id)
    logger.info("run finished", run_id=run_id, outcome=outcome)
    # If this run backs a PM project task (including approval-resumed runs),
    # map the outcome onto it. No-op for ordinary runs.
    from app.services.project_scheduler import finalize_task_for_run

    await finalize_task_for_run(run_id)
    return outcome


@broker.task(retry_on_error=False)
async def execute_project_task_attempt(task_id: str, worker_id: str) -> str:
    from app.services.project_scheduler import execute_task_attempt

    outcome = await execute_task_attempt(task_id, worker_id)
    logger.info("project task attempt finished", task_id=task_id, outcome=outcome)
    return outcome


@broker.task(retry_on_error=False)
async def schedule_project_task(project_id: str) -> int:
    from app.services.project_scheduler import schedule_project

    dispatched = await schedule_project(project_id)
    return len(dispatched)


@broker.task(retry_on_error=False)
async def ingest_document_task(document_id: str) -> str:
    from app.rag.ingestion import ingest_document

    outcome = await ingest_document(document_id)
    logger.info("ingestion finished", document_id=document_id, outcome=outcome)
    return outcome
