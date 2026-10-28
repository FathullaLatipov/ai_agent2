"""FastAPI application factory."""

import sys
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis
from app.db.capabilities import detect_capabilities
from app.db.session import dispose_engine, get_engine

logger = get_logger("agentos.api")

if sys.platform == "win32":  # pragma: no cover
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.env)
    from app.core.config import validate_production_config

    for warning in validate_production_config(settings):  # raises on fatal issues
        logger.warning("production config warning", detail=warning)
    caps = await detect_capabilities(get_engine())
    import app.tools.builtin  # noqa: F401 — register built-in tools
    from app.db.session import get_sessionmaker
    from app.services.agents_sync import sync_system_agents

    try:
        async with get_sessionmaker()() as session:
            changed = await sync_system_agents(session)
            await session.commit()
        if changed:
            logger.info("system agents synced", changed=changed)
    except Exception:
        # Tables may not exist yet (pre-migration boot) — endpoints degrade gracefully.
        logger.warning("system agent sync skipped (run alembic upgrade head)")

    # The API enqueues background tasks, so the broker must be started in
    # client mode (the worker process starts it in worker mode itself).
    from app.worker.broker import broker

    if not broker.is_worker_process:
        await broker.startup()
    logger.info(
        "startup",
        env=settings.env,
        pgvector=caps.pgvector,
        llm_configured=settings.llm_configured,
        embeddings_provider=settings.embeddings_provider,
    )
    yield
    if not broker.is_worker_process:
        await broker.shutdown()
    await dispose_engine()
    await close_redis()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AgentOS API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        correlation_id = request.headers.get("x-correlation-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id, path=request.url.path, method=request.method
        )
        # Request size limit (uploads have their own, larger limit).
        limit = (
            settings.max_upload_bytes
            if request.url.path.startswith("/api/v1/documents")
            else settings.max_request_bytes
        )
        length = request.headers.get("content-length")
        if length and int(length) > limit:
            return JSONResponse(
                {"detail": "request body too large"},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error")
            return JSONResponse(
                {"detail": "internal server error", "correlation_id": correlation_id},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-correlation-id"] = correlation_id
        return response

    from app.api.v1 import (
        agents,
        approvals,
        auth,
        documents,
        health,
        members,
        meta,
        projects,
        runs,
        tools,
    )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(meta.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(tools.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(members.router, prefix="/api/v1")

    return app


app = create_app()
