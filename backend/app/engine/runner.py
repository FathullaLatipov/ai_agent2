"""Run orchestration: invoked by the worker to start or resume a run.

The run-level wall-clock budget counts *active* execution time: time spent
waiting for human approval does not burn budget. Elapsed seconds accumulate in
run.budgets["elapsed_s"] across invocations.
"""

import asyncio
import datetime as dt
import time
import uuid

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.engine.errors import EngineError
from app.engine.graph import ExecutionEnv, build_graph, default_env
from app.events.recorder import EventRecorder
from app.models import ACTIVE_RUN_STATUSES, AgentRun, RunStatus

logger = get_logger("agentos.runner")

GRAPH_RECURSION_LIMIT = 200


async def execute_run(run_id: str, env: ExecutionEnv | None = None) -> str:
    """Start or resume a run. Returns the terminal (or pause) outcome string."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    env = env or default_env(sessionmaker, settings)

    async with sessionmaker() as session:
        run = await session.get(AgentRun, uuid.UUID(run_id))
        if run is None:
            logger.warning("run not found", run_id=run_id)
            return "not_found"
        if run.status not in ACTIVE_RUN_STATUSES:
            logger.info("run not executable", run_id=run_id, status=run.status.value)
            return f"skipped:{run.status.value}"
        if run.started_at is None:
            run.started_at = dt.datetime.now(dt.UTC)
            await env.recorder.emit(
                session,
                run_id=run.id,
                organization_id=run.organization_id,
                type="run.started",
                payload={"mode": run.mode.value},
            )
        elapsed_before = float((run.budgets or {}).get("elapsed_s", 0.0))
        await session.commit()
        await env.recorder.publish_pending()

    remaining = settings.run_timeout_s - elapsed_before
    if remaining <= 0:
        return await _fail_with(env, run_id, "run execution budget exceeded")

    graph = build_graph(env)
    started = time.monotonic()
    outcome = "unknown"
    try:
        async with asyncio.timeout(remaining):
            result = await graph.ainvoke(
                {"run_id": run_id}, config={"recursion_limit": GRAPH_RECURSION_LIMIT}
            )
        outcome = result.get("outcome", "unknown")
    except TimeoutError:
        outcome = await _fail_with(env, run_id, "run execution budget exceeded")
    except EngineError as exc:
        outcome = await _fail_with(env, run_id, str(exc))
    except Exception:
        logger.exception("engine crashed", run_id=run_id)
        outcome = await _fail_with(env, run_id, "internal engine error")
    finally:
        await _accumulate_elapsed(run_id, time.monotonic() - started)
    return outcome


async def _fail_with(env: ExecutionEnv, run_id: str, error: str) -> str:
    sessionmaker = get_sessionmaker()
    recorder = EventRecorder()
    async with sessionmaker() as session:
        run = await session.get(AgentRun, uuid.UUID(run_id))
        if run is None:
            return "not_found"
        if run.status in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            return run.status.value
        run.status = RunStatus.FAILED
        run.error = error
        run.finished_at = dt.datetime.now(dt.UTC)
        await recorder.emit(
            session,
            run_id=run.id,
            organization_id=run.organization_id,
            type="run.failed",
            payload={"error": error},
        )
        await session.commit()
        await recorder.publish_pending()
    return "failed"


async def _accumulate_elapsed(run_id: str, seconds: float) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        run = await session.get(AgentRun, uuid.UUID(run_id))
        if run is None:
            return
        budgets = dict(run.budgets or {})
        budgets["elapsed_s"] = round(float(budgets.get("elapsed_s", 0.0)) + seconds, 3)
        run.budgets = budgets
        await session.commit()
