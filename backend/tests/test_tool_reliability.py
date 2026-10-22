"""Tool-execution reliability & security audit tests (deterministic, local).

Everything here runs through the real services and execution engine; no
external services are involved, and no test claims external connectivity.
"""

import asyncio
import io
import uuid

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import select

import app.tools.builtin  # noqa: F401 — register built-ins
from app.agents.definitions import SYSTEM_AGENTS
from app.core.logging import REDACTED
from app.events.recorder import EventRecorder
from app.models import DocumentChunk, ExecutionEvent, ToolCall, ToolCallStatus
from app.services.acceptance import _evaluate_criterion
from app.tools.base import ToolContext, ToolError, ToolSpec, ToolTransientError
from app.tools.registry import ToolRegistry
from app.tools.registry import registry as global_registry
from tests.conftest import register_second_org
from tests.helpers import make_org
from tests.test_pm_harness import _add_task, _create_project, _detail, _task_by_ordinal

# ------------------------------------------------ registry ↔ capability audit


def test_every_agent_allowlisted_tool_is_registered():
    """Capability claims must be backed by real registered tools."""
    registered = {spec.name for spec in global_registry.list_specs()}
    for agent in SYSTEM_AGENTS.values():
        missing = set(agent.allowed_tools) - registered
        assert not missing, f"agent '{agent.key}' allows unregistered tools: {missing}"


def test_every_registered_tool_has_schemas_and_permission():
    for spec in global_registry.list_specs():
        assert spec.name and spec.description
        assert spec.input_model.model_json_schema()
        assert spec.output_model.model_json_schema()
        assert spec.required_permission
        assert spec.timeout_s > 0


# ------------------------------------------------ invocation lifecycle


class _In(BaseModel):
    password: str = "x"


class _Out(BaseModel):
    ok: bool = True


def _ctx(session, org_id, run=None) -> ToolContext:
    return ToolContext(
        session=session,
        organization_id=org_id,
        run_id=run.id if run else None,
        task_id=None,
        agent_key="executor",
    )


async def test_non_idempotent_timeout_is_not_retryable(db_session):
    async def slow(_ctx, _args):
        await asyncio.sleep(0.4)
        return _Out()

    local = ToolRegistry()
    local.register(
        ToolSpec(
            name="test.slow_send",
            description="non-idempotent slow op",
            input_model=_Out,  # no args needed
            output_model=_Out,
            handler=slow,
            timeout_s=0.05,
            idempotent=False,
        )
    )
    org, _ = await make_org(db_session)
    with pytest.raises(ToolError) as excinfo:
        await local.execute(
            _ctx(db_session, org.id), tool_name="test.slow_send", arguments={},
            allowed_tools=None,
        )
    # Crucially NOT the retryable class — the engine must not re-send.
    assert not isinstance(excinfo.value, ToolTransientError)
    assert "not idempotent" in str(excinfo.value)
    call = (
        await db_session.scalars(
            select(ToolCall).where(ToolCall.tool_name == "test.slow_send")
        )
    ).first()
    assert call.status == ToolCallStatus.TIMED_OUT


async def test_idempotent_timeout_stays_retryable(db_session):
    async def slow(_ctx, _args):
        await asyncio.sleep(0.4)
        return _Out()

    local = ToolRegistry()
    local.register(
        ToolSpec(
            name="test.slow_read",
            description="idempotent slow op",
            input_model=_Out,
            output_model=_Out,
            handler=slow,
            timeout_s=0.05,
            idempotent=True,
        )
    )
    org, _ = await make_org(db_session)
    with pytest.raises(ToolTransientError):
        await local.execute(
            _ctx(db_session, org.id), tool_name="test.slow_read", arguments={},
            allowed_tools=None,
        )


async def test_tool_crash_yields_safe_error(db_session):
    async def boom(_ctx, _args):
        raise RuntimeError("secret-internal-path C:/creds.txt")

    local = ToolRegistry()
    local.register(
        ToolSpec(
            name="test.boom",
            description="crashes",
            input_model=_Out,
            output_model=_Out,
            handler=boom,
            timeout_s=5,
        )
    )
    org, _ = await make_org(db_session)
    with pytest.raises(ToolError) as excinfo:
        await local.execute(
            _ctx(db_session, org.id), tool_name="test.boom", arguments={},
            allowed_tools=None,
        )
    assert "secret-internal-path" not in str(excinfo.value)  # sanitized
    call = (
        await db_session.scalars(select(ToolCall).where(ToolCall.tool_name == "test.boom"))
    ).first()
    assert call.status == ToolCallStatus.FAILED
    assert call.error == "internal tool error"  # raw exception never persisted


async def test_redact_fields_sanitize_persisted_args_and_events(db_session):
    captured = {}

    async def handler(_ctx, args: _In):
        captured["password"] = args.password  # the tool itself still gets the value
        return _Out()

    local = ToolRegistry()
    local.register(
        ToolSpec(
            name="test.secretive",
            description="takes a sensitive arg",
            input_model=_In,
            output_model=_Out,
            handler=handler,
            timeout_s=5,
            redact_fields=("password",),
        )
    )
    org, user = await make_org(db_session)
    from app.services.runs import create_run

    recorder = EventRecorder()
    run = await create_run(
        db_session, recorder, organization_id=org.id, user_id=user.id, input_text="x"
    )
    await local.execute(
        _ctx(db_session, org.id, run),
        tool_name="test.secretive",
        arguments={"password": "hunter2-super-secret"},
        allowed_tools=None,
        recorder=recorder,
    )
    assert captured["password"] == "hunter2-super-secret"

    call = (
        await db_session.scalars(
            select(ToolCall).where(ToolCall.tool_name == "test.secretive")
        )
    ).first()
    assert call.arguments["password"] == REDACTED

    events = (
        await db_session.scalars(
            select(ExecutionEvent).where(
                ExecutionEvent.run_id == run.id, ExecutionEvent.type == "tool.called"
            )
        )
    ).all()
    assert events and all(
        e.payload["arguments"]["password"] == REDACTED for e in events
    )
    assert "hunter2" not in str([e.payload for e in events])


# ------------------------------------------------ acceptance hardening


def test_hollow_artifacts_do_not_satisfy_acceptance():
    hollow = {"artifacts": [{"kind": "findings", "data": {"summary": "", "findings": []}}]}
    passed, _detail = _evaluate_criterion({"kind": "artifact_exists"}, hollow)
    assert passed is False
    real = {"artifacts": [{"kind": "findings", "data": {"summary": "actual content"}}]}
    passed, _ = _evaluate_criterion({"kind": "artifact_exists"}, real)
    assert passed is True


async def test_agent_prose_without_content_triggers_rework_then_failure(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """An agent 'claiming success' with a hollow result never counts as done."""
    from app.engine import graph as graph_module
    from app.schemas.agents import ResearchFindings

    async def hollow_research(_ctx):
        return ResearchFindings(
            summary="", findings=[], evidence=[], assumptions=[], missing_information=[]
        )

    monkeypatch.setitem(graph_module.AGENT_RUNNERS, "research", hollow_research)
    project = await _create_project(client, auth_headers, "Hollow", "Investigate topic epsilon")
    await _add_task(
        client, auth_headers, project["id"],
        title="Investigate epsilon", agent_key="research",
    )
    await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)
    detail = await _detail(client, auth_headers, project["id"])
    task = _task_by_ordinal(detail, 1)
    assert task["status"] == "failed"
    assert task["attempts"] == task["max_attempts"]  # bounded rework happened
    assert "non-empty deliverable" in task["error"]


# ------------------------------------------------ provenance & tenancy


async def test_cross_tenant_supplied_artifact_not_usable(
    client: AsyncClient, auth_headers: dict
):
    """A document id from another org must not satisfy reviewer prerequisites."""
    other = await register_second_org(client)
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("foreign.md", io.BytesIO(b"# Foreign doc\n\ncontent"), "text/markdown")},
        headers=other,
    )
    foreign_doc_id = upload.json()["id"]

    project = await _create_project(client, auth_headers, "Tenant check", "review something")
    task = await _add_task(
        client, auth_headers, project["id"],
        title="Review foreign artifact", agent_key="reviewer",
        supplied_artifacts=[{"kind": "document", "ref_id": foreign_doc_id, "label": "foreign"}],
    )
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/assign",
        json={"agent_key": "reviewer", "mode": "start_now"},
        headers=auth_headers,
    )
    body = resp.json()
    assert body["status"] == "blocked"  # the foreign document did not count
    detail = await _detail(client, auth_headers, project["id"])
    assert _task_by_ordinal(detail, 1)["run_id"] is None  # reviewer never executed


# ------------------------------------------------ vertical slice (Phase 2)


async def test_vertical_slice_real_tool_execution_with_provenance(
    client: AsyncClient, auth_headers: dict, db_session
):
    """Smallest real workflow, fully local and deterministic:

    upload → project → PM plan → owner approval → scheduler → research agent
    invokes the real kb.search tool over real ingested chunks → artifact with
    evidence + provenance persisted → reviewer receives it via the dependency
    mechanism and verifies the evidence rows exist → project completed.
    """
    policy = (
        "Travel Policy\n\nBusiness class is permitted only for flights longer than "
        "six hours and requires VP approval.\n\nEconomy class applies otherwise.\n"
    )
    upload = await client.post(
        "/api/v1/documents",
        files={"file": ("travel_policy.md", io.BytesIO(policy.encode()), "text/markdown")},
        headers=auth_headers,
    )
    assert upload.status_code == 200 and upload.json()["status"] == "ready"

    project = await _create_project(
        client, auth_headers, "Policy answer",
        "What does our travel policy say about business class flights?",
    )
    resp = await client.post(f"/api/v1/projects/{project['id']}/plan", headers=auth_headers)
    assert resp.status_code == 200
    specs = resp.json()["tasks_spec"]
    assert [s["agent"] for s in specs] == ["research", "reviewer"]

    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed", detail["task_counts"]
    research = _task_by_ordinal(detail, 1)
    reviewer = _task_by_ordinal(detail, 2)
    assert research["status"] == "succeeded" and reviewer["status"] == "succeeded"

    # 1) The real tool ran through the engine and is persisted with status+timing.
    kb_calls = (
        await db_session.scalars(
            select(ToolCall).where(
                ToolCall.run_id == uuid.UUID(research["run_id"]),
                ToolCall.tool_name == "kb.search",
            )
        )
    ).all()
    assert len(kb_calls) == 1
    assert kb_calls[0].status == ToolCallStatus.COMPLETED
    assert kb_calls[0].duration_ms is not None

    # 2) The artifact carries real evidence: chunk ids that exist in this org.
    task_resp = await client.get(
        f"/api/v1/projects/{project['id']}/tasks/{research['id']}", headers=auth_headers
    )
    artifact = task_resp.json()["result"]["artifacts"][0]
    assert artifact["kind"] == "findings"
    evidence = artifact["evidence"]
    assert evidence, "research must cite retrieved chunks"
    for ref in evidence:
        assert ref["kind"] == "document_chunk"
        chunk = await db_session.get(DocumentChunk, uuid.UUID(ref["ref_id"]))
        assert chunk is not None
        assert "travel" in chunk.meta.get("filename", "").lower()

    # 3) Provenance is stamped and preserved.
    prov = artifact["provenance"]
    assert prov["agent"] == "research"
    assert prov["task_ordinal"] == 1
    assert prov["run_id"] == research["run_id"]
    assert prov["attempt"] == 1

    # 4) The reviewer received the artifact through the dependency mechanism
    #    (its run input, not conversation text) and verified the evidence.
    from app.models import RunTask

    reviewer_inputs = (
        await db_session.scalars(
            select(RunTask).where(
                RunTask.run_id == uuid.UUID(reviewer["run_id"]),
                RunTask.agent_key == "reviewer",
            )
        )
    ).first()
    handed = reviewer_inputs.input["dependency_artifacts"]
    assert handed[0]["provenance"]["task_ordinal"] == 1

    reviewer_detail = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks/{reviewer['id']}", headers=auth_headers
        )
    ).json()
    assert reviewer_detail["reviews"] and reviewer_detail["reviews"][-1]["passed"] is True
    assert reviewer_detail["result"]["artifacts"][0]["kind"] == "review"
