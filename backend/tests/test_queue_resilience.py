"""Redis/broker unavailability during submission (Stage 2 regression tests).

Failure is simulated at the enqueue boundary (kiq raising SendTaskError — the
exact exception taskiq raises when Redis is down) and every test goes through
the real API handlers/services, not helpers.
"""

import io

import pytest
from httpx import AsyncClient
from taskiq.exceptions import SendTaskError

from app.worker import tasks as worker_tasks
from tests.test_pm_harness import _add_task, _create_project, _detail, _task_by_ordinal


async def _kiq_down(*_args, **_kwargs):
    raise SendTaskError("redis unavailable (simulated)")


@pytest.fixture
def queue_down(monkeypatch):
    """Make every enqueue fail, as if Redis were unreachable."""
    monkeypatch.setattr(worker_tasks.execute_run_task, "kiq", _kiq_down)
    monkeypatch.setattr(worker_tasks.execute_project_task_attempt, "kiq", _kiq_down)
    monkeypatch.setattr(worker_tasks.ingest_document_task, "kiq", _kiq_down)
    monkeypatch.setattr(worker_tasks.schedule_project_task, "kiq", _kiq_down)
    return monkeypatch


async def test_submit_run_queue_down_fails_honestly_then_recovers(
    client: AsyncClient, auth_headers: dict, queue_down
):
    resp = await client.post(
        "/api/v1/runs", json={"input_text": "What is our refund policy?"}, headers=auth_headers
    )
    # Before the fix: HTTP 500 + an orphaned run stuck in 'queued' forever.
    assert resp.status_code == 503
    assert "queue unavailable" in resp.json()["detail"]

    runs = (await client.get("/api/v1/runs", headers=auth_headers)).json()
    assert runs["total"] == 1  # exactly one record, never duplicated
    run = runs["items"][0]
    assert run["status"] == "failed"  # honest terminal state, NOT 'queued'

    events = (
        await client.get(f"/api/v1/runs/{run['id']}/events", headers=auth_headers)
    ).json()
    assert events[-1]["type"] == "run.failed"
    assert "queue" in events[-1]["payload"]["error"]

    # Queue restored → the documented recovery path works end to end.
    queue_down.undo()
    retried = await client.post(f"/api/v1/runs/{run['id']}/retry", headers=auth_headers)
    assert retried.status_code == 200
    detail = (await client.get(f"/api/v1/runs/{run['id']}", headers=auth_headers)).json()
    assert detail["status"] == "completed"
    assert runs["total"] == 1  # same run recovered; no duplicate execution record


async def test_retry_endpoint_queue_down(client: AsyncClient, auth_headers: dict, monkeypatch):
    from app.engine import graph as graph_module
    from app.engine.errors import TaskFailedError

    async def always_fail(_ctx):
        raise TaskFailedError("synthetic failure")

    monkeypatch.setitem(graph_module.AGENT_RUNNERS, "research", always_fail)
    resp = await client.post(
        "/api/v1/runs", json={"input_text": "What is our travel policy?"}, headers=auth_headers
    )
    run_id = resp.json()["id"]
    assert (
        await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
    ).json()["status"] == "failed"
    monkeypatch.undo()

    monkeypatch.setattr(worker_tasks.execute_run_task, "kiq", _kiq_down)
    retried = await client.post(f"/api/v1/runs/{run_id}/retry", headers=auth_headers)
    assert retried.status_code == 503
    detail = (await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)).json()
    assert detail["status"] == "failed"
    assert "queue unavailable" in detail["error"]

    monkeypatch.undo()
    retried = await client.post(f"/api/v1/runs/{run_id}/retry", headers=auth_headers)
    assert retried.status_code == 200
    assert (
        await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
    ).json()["status"] == "completed"


async def test_scheduler_releases_claim_when_queue_down(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    project = await _create_project(client, auth_headers, "Queue drop", "Investigate zeta")
    await _add_task(
        client, auth_headers, project["id"],
        title="Investigate zeta", agent_key="research",
    )
    monkeypatch.setattr(worker_tasks.execute_project_task_attempt, "kiq", _kiq_down)
    resp = await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)
    assert resp.status_code == 200
    assert "dispatched 0" in resp.json()["message"]

    task = _task_by_ordinal(await _detail(client, auth_headers, project["id"]), 1)
    # No stuck 'assigned' claim waiting for the TTL sweep — honestly READY.
    assert task["status"] == "ready"
    assert task["attempts"] == 0  # the attempt never ran, so it isn't counted

    monkeypatch.undo()
    await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)
    task = _task_by_ordinal(await _detail(client, auth_headers, project["id"]), 1)
    assert task["status"] == "succeeded"


async def test_approval_resume_queue_down_requires_fresh_authorization(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Decision is recorded; resume fails honestly; recovery creates a FRESH
    approval request — a cancelled/lost resume can never silently execute the
    gated tool on stale authorization."""
    project = await _create_project(
        client, auth_headers, "CRM queue drop",
        "Prepare a customer follow-up for CUST-1001 about the renewal and send it",
    )
    await client.post(
        f"/api/v1/projects/{project['id']}/pm/messages",
        json={"content": "create a plan"},
        headers=auth_headers,
    )
    await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    detail = await _detail(client, auth_headers, project["id"])
    assert _task_by_ordinal(detail, 2)["status"] == "waiting_approval"
    first_run_id = _task_by_ordinal(detail, 2)["run_id"]

    pending = (
        await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
    ).json()
    approval = next(a for a in pending if a["run_id"] == first_run_id)

    monkeypatch.setattr(worker_tasks.execute_run_task, "kiq", _kiq_down)
    decided = await client.post(
        f"/api/v1/approvals/{approval['id']}/decide",
        json={"decision": "approve"},
        headers=auth_headers,
    )
    assert decided.status_code == 200  # the decision itself IS recorded
    assert decided.json()["status"] == "approved"

    detail = await _detail(client, auth_headers, project["id"])
    task2 = _task_by_ordinal(detail, 2)
    assert task2["status"] == "failed"
    assert "queue unavailable" in task2["error"]

    # Queue restored → retry re-executes with a NEW run and a NEW approval.
    monkeypatch.undo()
    task_id = task2["id"]
    retried = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task_id}/retry", headers=auth_headers
    )
    assert retried.status_code == 200
    detail = await _detail(client, auth_headers, project["id"])
    task2 = _task_by_ordinal(detail, 2)
    assert task2["status"] == "waiting_approval"
    assert task2["run_id"] != first_run_id  # fresh run
    fresh_pending = [
        a
        for a in (
            await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
        ).json()
        if a["run_id"] == task2["run_id"]
    ]
    assert len(fresh_pending) == 1  # re-authorization required, nothing silent

    await client.post(
        f"/api/v1/approvals/{fresh_pending[0]['id']}/decide",
        json={"decision": "approve"},
        headers=auth_headers,
    )
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed"


async def test_document_upload_queue_down_marks_failed(
    client: AsyncClient, auth_headers: dict, queue_down
):
    resp = await client.post(
        "/api/v1/documents",
        files={"file": ("policy.md", io.BytesIO(b"# Policy\n\ntext"), "text/markdown")},
        headers=auth_headers,
    )
    assert resp.status_code == 200  # the file/row were persisted
    doc = resp.json()
    assert doc["status"] == "failed"  # never an eternally-'pending' orphan
    assert "queue unavailable" in doc["error"]


async def test_plan_approval_survives_queue_down(
    client: AsyncClient, auth_headers: dict, queue_down
):
    """Approving a plan when the queue is down: tasks materialize READY and are
    dispatchable later — approval is not lost and nothing is stuck claimed."""
    project = await _create_project(client, auth_headers, "Plan kick", "Investigate eta")
    await client.post(f"/api/v1/projects/{project['id']}/plan", headers=auth_headers)
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "active"
    assert all(t["status"] in ("planned", "ready", "blocked") for t in detail["tasks"])

    queue_down.undo()
    await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed"
