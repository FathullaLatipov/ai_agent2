"""Runtime capability detection (reported honestly — never assumed)."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class DbCapabilities:
    pgvector: bool = False


_capabilities: DbCapabilities | None = None


async def detect_capabilities(engine: AsyncEngine) -> DbCapabilities:
    """Detect optional database features. Called once at startup."""
    global _capabilities
    caps = DbCapabilities()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        caps.pgvector = result.scalar() is not None
    _capabilities = caps
    return caps


def get_capabilities() -> DbCapabilities:
    """Return detected capabilities; defaults to the conservative baseline if not detected."""
    return _capabilities or DbCapabilities()
