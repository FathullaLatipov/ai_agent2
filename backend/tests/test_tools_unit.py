"""Tool system: calculator safety, registry enforcement (allowlist, validation,
timeout), tool_calls persistence."""

import asyncio

import pytest
from pydantic import BaseModel
from sqlalchemy import select

import app.tools.builtin  # noqa: F401 — register built-ins
from app.models import ToolCall, ToolCallStatus
from app.tools.base import (
    ToolContext,
    ToolInputError,
    ToolPermissionError,
    ToolSpec,
    ToolTransientError,
)
from app.tools.registry import ToolRegistry
from app.tools.registry import registry as global_registry
from tests.helpers import make_org


def _ctx(session, org_id) -> ToolContext:
    return ToolContext(
        session=session, organization_id=org_id, run_id=None, task_id=None, agent_key="analyst"
    )


async def test_calculator_evaluates(db_session):
    org, _ = await make_org(db_session)
    result = await global_registry.execute(
        _ctx(db_session, org.id),
        tool_name="calculator.evaluate",
        arguments={"expression": "round((1200.5 - 200.5) * 0.3, 2)"},
        allowed_tools=["calculator.evaluate"],
    )
    assert result.output.result == 300.0


@pytest.mark.parametrize(
    "expression",
    ["__import__('os')", "().__class__", "a + 1", "2 ** 99999", "1/0", "open('x')"],
)
async def test_calculator_rejects_unsafe(db_session, expression):
    org, _ = await make_org(db_session)
    with pytest.raises(ToolInputError):
        await global_registry.execute(
            _ctx(db_session, org.id),
            tool_name="calculator.evaluate",
            arguments={"expression": expression},
            allowed_tools=["calculator.evaluate"],
        )


async def test_registry_enforces_allowlist(db_session):
    org, _ = await make_org(db_session)
    with pytest.raises(ToolPermissionError):
        await global_registry.execute(
            _ctx(db_session, org.id),
            tool_name="calculator.evaluate",
            arguments={"expression": "1+1"},
            allowed_tools=["kb.search"],  # calculator not allowed
        )


async def test_registry_rejects_unknown_tool_and_bad_args(db_session):
    org, _ = await make_org(db_session)
    with pytest.raises(ToolInputError):
        await global_registry.execute(
            _ctx(db_session, org.id), tool_name="nope.missing", arguments={}, allowed_tools=None
        )
    with pytest.raises(ToolInputError):
        await global_registry.execute(
            _ctx(db_session, org.id),
            tool_name="calculator.evaluate",
            arguments={"wrong_field": "1"},
            allowed_tools=None,
        )


class _SlowIn(BaseModel):
    pass


class _SlowOut(BaseModel):
    ok: bool = True


async def test_registry_enforces_timeout(db_session):
    async def slow_handler(_ctx, _args):
        await asyncio.sleep(0.5)
        return _SlowOut()

    local = ToolRegistry()
    local.register(
        ToolSpec(
            name="test.slow",
            description="sleeps",
            input_model=_SlowIn,
            output_model=_SlowOut,
            handler=slow_handler,
            timeout_s=0.05,
        )
    )
    org, _ = await make_org(db_session)
    with pytest.raises(ToolTransientError):
        await local.execute(
            _ctx(db_session, org.id), tool_name="test.slow", arguments={}, allowed_tools=None
        )
    call = (
        await db_session.scalars(select(ToolCall).where(ToolCall.tool_name == "test.slow"))
    ).first()
    assert call is not None
    assert call.status == ToolCallStatus.TIMED_OUT


async def test_tool_call_persisted_with_result(db_session):
    org, _ = await make_org(db_session)
    await global_registry.execute(
        _ctx(db_session, org.id),
        tool_name="calculator.evaluate",
        arguments={"expression": "6*7"},
        allowed_tools=None,
    )
    call = (
        await db_session.scalars(select(ToolCall).where(ToolCall.organization_id == org.id))
    ).first()
    assert call.status == ToolCallStatus.COMPLETED
    assert call.result_summary["result"] == 42.0
    assert call.duration_ms is not None
