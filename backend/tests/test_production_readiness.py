"""Production-readiness guards: config validation and recovery wiring."""

import pytest

from app.core.config import ProductionConfigError, Settings, validate_production_config


def _settings(**overrides) -> Settings:
    base = {
        "env": "production",
        "secret_key": "a" * 48,
        "database_url": "postgresql+asyncpg://app:strongpass@db:5432/agentos",
        "frontend_origin": "https://agentos.example.com",
    }
    base.update(overrides)
    return Settings(**base)


def test_development_config_is_not_policed():
    dev = _settings(env="development", secret_key="dev-key-just-16ch")
    assert validate_production_config(dev) == []


def test_production_refuses_example_secret():
    with pytest.raises(ProductionConfigError, match="AGENTOS_SECRET_KEY"):
        validate_production_config(
            _settings(secret_key="change-me-to-a-long-random-string-xxxxxx")
        )


def test_production_refuses_short_secret():
    with pytest.raises(ProductionConfigError, match="32 chars"):
        validate_production_config(_settings(secret_key="only-twenty-chars-xx"))


def test_production_warns_on_localhost_cors_and_default_db_creds():
    warnings = validate_production_config(
        _settings(
            frontend_origin="https://app.example.com,http://localhost:3000",
            database_url="postgresql+asyncpg://postgres:postgres@db:5432/agentos",
        )
    )
    assert any("localhost" in w for w in warnings)
    assert any("postgres/postgres" in w for w in warnings)


def test_clean_production_config_passes_without_warnings():
    assert validate_production_config(_settings()) == []


async def test_worker_startup_recovery_hook_runs(db_session):
    """The startup hook actually sweeps stale claims (wiring, not just existence)."""
    import datetime as dt

    from app.models import PMTaskStatus
    from app.services.project_events import ProjectEventRecorder
    from app.services.project_scheduler import claim_task
    from app.services.projects import create_project, create_task
    from app.services.task_state import transition
    from app.worker.tasks import recover_on_startup
    from tests.helpers import make_org

    org, user = await make_org(db_session)
    recorder = ProjectEventRecorder()
    project = await create_project(
        db_session, recorder, organization_id=org.id, user_id=user.id,
        name="Startup recovery", goal="g",
    )
    project.status = "active"
    task = await create_task(
        db_session, recorder, project=project, user_id=user.id,
        title="stale task", agent_key="research",
    )
    transition(task, PMTaskStatus.READY)
    await db_session.commit()
    assert await claim_task(db_session, task.id, "dead-worker")
    await db_session.refresh(task)
    task.claimed_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=3)
    await db_session.commit()

    await recover_on_startup(None)  # the actual taskiq WORKER_STARTUP handler

    await db_session.refresh(task)
    assert task.status == PMTaskStatus.READY
    assert task.claimed_by is None
