"""End-to-end engine tests through the real API with the inline test broker.

Everything here runs with NO LLM (deterministic mode) — per the definition of
done, deterministic tool execution must work without an LLM API key.
"""

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, MemberRole, RunStatus
from app.services.approvals import ApprovalError, decide_approval
from tests.conftest import register_second_org
from tests.helpers import seed_csv_document, write_sales_csv


async def _me_org_id(client: AsyncClient, headers: dict) -> str:
    me = await client.get("/api/v1/auth/me", headers=headers)
    return me.json()["memberships"][0]["organization"]["id"]


async def _submit(client: AsyncClient, headers: dict, text: str, **kwargs) -> dict:
    resp = await client.post(
        "/api/v1/runs", json={"input_text": text, **kwargs}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _detail(client: AsyncClient, headers: dict, run_id: str) -> dict:
    resp = await client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_kb_question_run_completes_with_honest_no_evidence(
    client: AsyncClient, auth_headers: dict
):
    run = await _submit(client, auth_headers, "What is our refund policy?")
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "completed"
    assert detail["mode"] == "deterministic"
    research_task = detail["tasks"][0]
    assert research_task["status"] == "completed"
    assert research_task["output"]["missing_information"]  # no docs -> honest gap
    assert detail["report"] is not None
    assert "Limitations" in detail["report"]["body_markdown"]
    # Event timeline is persisted, ordered, and complete.
    events = (await client.get(f"/api/v1/runs/{run['id']}/events", headers=auth_headers)).json()
    types = [e["type"] for e in events]
    assert types[0] == "run.created"
    assert "run.plan_created" in types
    assert "task.started" in types and "task.completed" in types
    assert types[-1] == "run.completed"
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_sales_csv_scenario_computes_real_metrics(
    client: AsyncClient, auth_headers: dict, db_session, tmp_path: Path
):
    org_id = await _me_org_id(client, auth_headers)
    csv_path = tmp_path / "sales.csv"
    write_sales_csv(csv_path)
    import uuid as uuid_mod

    await seed_csv_document(db_session, uuid_mod.UUID(org_id), csv_path)
    await db_session.commit()

    run = await _submit(
        client,
        auth_headers,
        "Analyze the last 30 days of sales and identify underperforming products",
    )
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "completed", detail["error"]
    analyst = detail["tasks"][0]["output"]
    metrics = {m["name"]: m["value"] for m in analyst["metrics"]}
    assert metrics["total_revenue"] == 30 * 3012.5  # exact deterministic value
    assert "Delta Widget" in analyst["summary"]  # flagged underperformer
    assert all(m["computed_by"] for m in analyst["metrics"])  # traceable to tools
    assert detail["report"]["body_markdown"].startswith("# Run report:")
    tool_names = {t["tool_name"] for t in detail["tool_calls"]}
    assert {"dataset.inspect", "dataset.stats", "dataset.aggregate"} <= tool_names


async def test_crm_approval_flow_approve(client: AsyncClient, auth_headers: dict, db_session):
    run = await _submit(
        client, auth_headers, "Prepare a customer follow-up for CUST-1001 and send it"
    )
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "waiting_approval"
    task_states = {t["ordinal"]: t["status"] for t in detail["tasks"]}
    assert task_states[0] == "completed"
    assert task_states[1] == "waiting_approval"

    approvals = (
        await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
    ).json()
    assert len(approvals) == 1
    assert approvals[0]["tool_name"] == "crm.send_followup"
    # The draft is visible for human review in the approval payload.
    assert approvals[0]["payload"]["arguments"]["customer_id"] == "CUST-1001"

    decided = await client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decide",
        json={"decision": "approve", "reason": "looks good"},
        headers=auth_headers,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"

    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "completed", detail["error"]
    sent_action = detail["tasks"][1]["output"]["actions"][0]
    assert sent_action["type"] == "sent_followup"

    # The mock-CRM side effect is real and inspectable.
    outbox = Path(get_settings().upload_dir).parent / "crm_outbox.jsonl"
    lines = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert any(entry["delivery_id"] == sent_action["delivery_id"] for entry in lines)

    # Audit trail covers the decision.
    audits = (
        await db_session.scalars(select(AuditLog).where(AuditLog.action == "approval.approved"))
    ).all()
    assert len(audits) == 1


async def test_crm_approval_flow_reject(client: AsyncClient, auth_headers: dict):
    run = await _submit(
        client, auth_headers, "Prepare a customer follow-up for CUST-1002 and send it"
    )
    approvals = (
        await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
    ).json()
    decided = await client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decide",
        json={"decision": "reject", "reason": "not appropriate"},
        headers=auth_headers,
    )
    assert decided.status_code == 200
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "failed"
    assert "rejected" in detail["tasks"][1]["error"]


async def test_approval_requires_admin_role(db_session):
    import datetime as dt
    import uuid as uuid_mod

    from app.events.recorder import EventRecorder
    from app.models import ApprovalRequest, ApprovalStatus
    from tests.helpers import make_org

    org, user = await make_org(db_session)
    approval = ApprovalRequest(
        organization_id=org.id,
        run_id=None,
        tool_name="crm.send_followup",
        operation_summary="x",
        payload={},
        status=ApprovalStatus.PENDING,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    # run_id is non-nullable — create a real run row first.
    from app.services.runs import create_run

    run = await create_run(
        db_session,
        EventRecorder(),
        organization_id=org.id,
        user_id=user.id,
        input_text="test run",
    )
    approval.run_id = run.id
    db_session.add(approval)
    await db_session.flush()

    with pytest.raises(ApprovalError, match="admins or owners"):
        await decide_approval(
            db_session,
            EventRecorder(),
            approval=approval,
            decision="approve",
            user_id=uuid_mod.uuid4(),
            role=MemberRole.MEMBER,
        )


async def test_task_retry_on_transient_error(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """First attempt fails transiently; the engine retries and the run completes."""
    from app.agents.research import run_research
    from app.engine import graph as graph_module
    from app.tools.base import ToolTransientError

    attempts = {"count": 0}

    async def flaky_research(ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ToolTransientError("synthetic transient failure")
        return await run_research(ctx)

    monkeypatch.setitem(graph_module.AGENT_RUNNERS, "research", flaky_research)
    run = await _submit(client, auth_headers, "What is our vacation policy?")
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "completed"
    assert attempts["count"] == 2
    events = (await client.get(f"/api/v1/runs/{run['id']}/events", headers=auth_headers)).json()
    assert any(e["type"] == "task.retrying" for e in events)


async def test_task_fails_after_retries_exhausted(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    from app.engine import graph as graph_module
    from app.tools.base import ToolTransientError

    async def always_failing(_ctx):
        raise ToolTransientError("permanently flaky")

    monkeypatch.setitem(graph_module.AGENT_RUNNERS, "research", always_failing)
    run = await _submit(client, auth_headers, "What is our travel policy?")
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "failed"
    assert "permanently flaky" in detail["error"]
    # Failed runs can be retried.
    monkeypatch.undo()
    retried = await client.post(f"/api/v1/runs/{run['id']}/retry", headers=auth_headers)
    assert retried.status_code == 200
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "completed"


async def test_cancellation_before_execution(db_session):
    """cancel_requested set before the engine runs → run finalizes as cancelled."""
    from app.engine.runner import execute_run
    from app.events.recorder import EventRecorder
    from app.services.runs import create_run
    from tests.helpers import make_org

    org, user = await make_org(db_session)
    run = await create_run(
        db_session,
        EventRecorder(),
        organization_id=org.id,
        user_id=user.id,
        input_text="What is our policy on cancellation?",
    )
    run.cancel_requested = True
    await db_session.commit()

    outcome = await execute_run(str(run.id))
    assert outcome == "cancelled"
    await db_session.refresh(run)
    assert run.status == RunStatus.CANCELLED


async def test_cancel_waiting_approval_run(client: AsyncClient, auth_headers: dict):
    run = await _submit(
        client, auth_headers, "Prepare a customer follow-up for CUST-1003 and send it"
    )
    detail = await _detail(client, auth_headers, run["id"])
    assert detail["status"] == "waiting_approval"
    cancelled = await client.post(f"/api/v1/runs/{run['id']}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


async def test_run_tenancy_isolation(client: AsyncClient, auth_headers: dict):
    run = await _submit(client, auth_headers, "What is our refund policy?")
    other_headers = await register_second_org(client)

    assert (
        await client.get(f"/api/v1/runs/{run['id']}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/runs/{run['id']}/events", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/runs/{run['id']}/cancel", headers=other_headers)
    ).status_code == 404
    listed = (await client.get("/api/v1/runs", headers=other_headers)).json()
    assert listed["total"] == 0


async def test_approval_tenancy_isolation(client: AsyncClient, auth_headers: dict):
    await _submit(client, auth_headers, "Prepare a customer follow-up for CUST-1001 and send it")
    approvals = (
        await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
    ).json()
    other_headers = await register_second_org(client)
    assert (
        await client.get("/api/v1/approvals?status=pending", headers=other_headers)
    ).json() == []
    denied = await client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decide",
        json={"decision": "approve"},
        headers=other_headers,
    )
    assert denied.status_code == 404


async def test_stream_token_scoped_to_run(client: AsyncClient, auth_headers: dict):
    run_a = await _submit(client, auth_headers, "What is our refund policy?")
    run_b = await _submit(client, auth_headers, "What is our pricing policy?")
    token = (
        await client.post(f"/api/v1/runs/{run_a['id']}/stream-token", headers=auth_headers)
    ).json()["token"]
    wrong = await client.get(f"/api/v1/runs/{run_b['id']}/events/stream?token={token}")
    assert wrong.status_code == 403


async def test_sse_stream_replays_and_closes_on_terminal_event(
    client: AsyncClient, auth_headers: dict
):
    run = await _submit(client, auth_headers, "What is our refund policy?")
    token = (
        await client.post(f"/api/v1/runs/{run['id']}/stream-token", headers=auth_headers)
    ).json()["token"]
    received = []
    async with client.stream(
        "GET", f"/api/v1/runs/{run['id']}/events/stream?token={token}"
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                received.append(line.split(":", 1)[1].strip())
    assert received[0] == "run.created"
    assert received[-1] == "run.completed"
