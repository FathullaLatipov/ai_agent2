"""Test fixtures.

- AGENTOS_ENV=test is forced before any app import; tests run against the
  dedicated test database, migrated with the real Alembic migrations once per
  session and truncated between tests.
- Requires local PostgreSQL and Redis (available in dev and CI).
"""

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("AGENTOS_SECRET_KEY", "test-secret-key-for-tests-only-0123456789")
os.environ["AGENTOS_ENV"] = "test"
os.environ["AGENTOS_LLM_PROVIDER"] = "none"
os.environ["AGENTOS_EMBEDDINGS_PROVIDER"] = "local-hash"
_TEST_VAR_DIR = tempfile.mkdtemp(prefix="agentos-tests-")
os.environ["AGENTOS_UPLOAD_DIR"] = os.path.join(_TEST_VAR_DIR, "uploads")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from alembic import command
from app.core.config import get_settings
from app.core.redis import close_redis
from app.db.session import dispose_engine, get_engine, get_sessionmaker

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

get_settings.cache_clear()
assert get_settings().env == "test"


@pytest.fixture(scope="session", autouse=True)
def migrated_test_db():
    """Reset the test database and apply real migrations once per session."""
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def clean_tables(migrated_test_db):
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename != 'alembic_version'"
                )
            )
        ).scalars().all()
        if tables:
            await conn.execute(
                text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE")
            )
    await dispose_engine()
    await close_redis()


@pytest.fixture
async def db_session():
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client():
    from app.main import create_app

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


REGISTER_BODY = {
    "email": "alice@example.com",
    "password": "correct-horse-battery",
    "full_name": "Alice Example",
    "organization_name": "Acme Corp",
}


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register a default user+org and return Authorization headers."""
    resp = await client.post("/api/v1/auth/register", json=REGISTER_BODY)
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def register_second_org(client: AsyncClient) -> dict:
    """A second, unrelated tenant for isolation tests."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "mallory@other.com",
            "password": "another-strong-pass",
            "full_name": "Mallory Other",
            "organization_name": "Other LLC",
        },
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
