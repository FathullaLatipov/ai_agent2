"""Liveness and readiness probes (unauthenticated)."""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.redis import get_redis
from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict:
    checks: dict[str, str] = {}
    ok = True
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"
        ok = False
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
        ok = False
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "checks": checks}
