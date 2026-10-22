"""PM harness acceptance scenarios (master prompt §18) — run through the real
API with the inline test broker. Everything executes deterministically (no LLM).
"""

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.agents.pm import PlanDraftError
from app.models import (
    AgentRun,
    PMTaskStatus,
    ProjectPlan,
    ProjectTask,
    RunStatus,
    TaskReview,
)
from app.schemas.pm import PlanDraft, TaskSpec
from app.services.pm import propose_plan
from app.services.project_events import ProjectEventRecorder
from app.services.project_scheduler import claim_task, recover_stale_tasks
from app.services.projects import create_project, create_task
from app.services.task_state import transition
from tests.conftest import register_second_org
from tests.helpers import make_org


async def _create_project(client: AsyncClient, headers: dict, name: str, goal: str) -> dict:
    resp = await client.post(
        "/api/v1/projects", json={"name": name, "goal": goal}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _detail(client: AsyncClient, headers: dict, project_id: str) -> dict:
    resp = await client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_task(client: AsyncClient, headers: dict, project_id: str, **body) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks", json=body, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _task_by_ordinal(detail: dict, ordinal: int) -> dict:
    return next(t for t in detail["tasks"] if t["ordinal"] == ordinal)


# ------------------------------------------------------------ Scenario A


async def test_scenario_a_reviewer_requested_too_early(
    client: AsyncClient, auth_headers: dict
):
    project = await _create_project(
        client, auth_headers, "Policy compliance", "Check our refund policy handling"
    )
    task = await _add_task(
        client,
        auth_headers,
        project["id"],
        title="Review refund policy findings",
        description="Verify that the refund policy findings are grounded in sources",
        agent_key="reviewer",
    )

    # Owner explicitly asks to start Reviewer now — prerequisites are missing.
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/assign",
        json={"agent_key": "reviewer", "mode": "start_now"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["plan_version"] == 1
    assert "cannot start" in body["message"].lower() or "requires" in body["message"].lower()
    assert "approve" in body["message"].lower()

    # Reviewer was NOT executed; the owner's intent is preserved.
    detail = await _detail(client, auth_headers, project["id"])
    reviewer_task = _task_by_ordinal(detail, 1)
    assert reviewer_task["run_id"] is None
    assert reviewer_task["requested_agent_key"] == "reviewer"
    assert reviewer_task["status"] not in ("running", "succeeded")
    plan = next(p for p in detail["plans"] if p["version"] == 1)
    assert plan["status"] == "proposed"
    assert plan["tasks_spec"][0]["agent"] == "research"  # proposed prerequisite

    # Owner approves the prerequisite plan → prereq runs first, then Reviewer.
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed"
    prereq = _task_by_ordinal(detail, 2)
    reviewer_task = _task_by_ordinal(detail, 1)
    assert prereq["agent_key"] == "research"
    assert prereq["status"] == "succeeded"
    assert reviewer_task["status"] == "succeeded"
    assert reviewer_task["depends_on"] == [2]

    # Order is verifiable from persisted events: reviewer started after prereq succeeded.
    events = (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=auth_headers)
    ).json()
    prereq_done_seq = next(
        e["seq"] for e in events if e["type"] == "task.succeeded" and e["payload"]["ordinal"] == 2
    )
    reviewer_start_seq = next(
        e["seq"] for e in events if e["type"] == "task.started" and e["payload"]["ordinal"] == 1
    )
    assert reviewer_start_seq > prereq_done_seq


# ------------------------------------------------------------ Scenario B


async def test_scenario_b_existing_artifacts_allow_immediate_review(
    client: AsyncClient, auth_headers: dict
):
    import io

    upload = await client.post(
        "/api/v1/documents",
        files={
            "file": (
                "deliverable.md",
                io.BytesIO(b"# Deliverable\n\nThe implementation artifact to review.\n"),
                "text/markdown",
            )
        },
        headers=auth_headers,
    )
    assert upload.status_code == 200
    doc_id = upload.json()["id"]

    project = await _create_project(
        client, auth_headers, "Direct review", "Review the supplied deliverable"
    )
    task = await _add_task(
        client,
        auth_headers,
        project["id"],
        title="Check supplied deliverable",
        description="Validate the provided artifact",
        agent_key="reviewer",
        supplied_artifacts=[{"kind": "document", "ref_id": doc_id, "label": "deliverable.md"}],
    )

    resp = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/assign",
        json={"agent_key": "reviewer", "mode": "start_now"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "started"  # no forced planning/implementation

    detail = await _detail(client, auth_headers, project["id"])
    reviewed = _task_by_ordinal(detail, 1)
    assert reviewed["status"] == "succeeded"
    assert len(detail["plans"]) == 0  # no unnecessary prerequisite proposal
    assert len(detail["tasks"]) == 1


# ------------------------------------------------------------ Scenario C


async def test_scenario_c_parallel_analyses_block_report(
    client: AsyncClient, auth_headers: dict
):
    project = await _create_project(
        client, auth_headers, "Parallel work", "Investigate two topics, then report"
    )
    a = await _add_task(
        client, auth_headers, project["id"],
        title="Investigate topic alpha",
        description="Collect knowledge-base context on topic alpha",
        agent_key="research",
    )
    b = await _add_task(
        client, auth_headers, project["id"],
        title="Investigate topic beta",
        description="Collect knowledge-base context on topic beta",
        agent_key="research",
    )
    report = await _add_task(
        client, auth_headers, project["id"],
        title="Combine into final summary",
        description="Summarize both investigations",
        agent_key="research",
        depends_on=[1, 2],
    )
    assert a["ordinal"] == 1 and b["ordinal"] == 2 and report["ordinal"] == 3

    resp = await client.post(
        f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text

    detail = await _detail(client, auth_headers, project["id"])
    assert all(t["status"] == "succeeded" for t in detail["tasks"]), detail["task_counts"]
    assert detail["status"] == "completed"

    events = (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=auth_headers)
    ).json()
    done_seqs = {
        e["payload"]["ordinal"]: e["seq"] for e in events if e["type"] == "task.succeeded"
    }
    report_start = next(
        e["seq"] for e in events if e["type"] == "task.started" and e["payload"]["ordinal"] == 3
    )
    assert report_start > done_seqs[1] and report_start > done_seqs[2]
    # Both independent tasks were dispatched before the report became eligible.
    blocked_report = [
        e for e in events if e["type"] == "task.blocked" and e["payload"]["ordinal"] == 3
    ]
    assert blocked_report, "report should have been explicitly blocked first"


async def test_scenario_c_failed_dependency_prevents_report(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    from app.agents.research import run_research
    from app.engine import graph as graph_module
    from app.engine.errors import TaskFailedError

    async def failing_alpha(ctx):
        if "alpha" in ctx.task.objective:
            raise TaskFailedError("synthetic permanent failure")
        return await run_research(ctx)

    monkeypatch.setitem(graph_module.AGENT_RUNNERS, "research", failing_alpha)

    project = await _create_project(
        client, auth_headers, "Failing dependency", "One analysis fails"
    )
    await _add_task(
        client, auth_headers, project["id"],
        title="Investigate topic alpha",
        description="This one fails: alpha",
        agent_key="research",
    )
    await _add_task(
        client, auth_headers, project["id"],
        title="Final summary",
        description="Summarize the alpha investigation",
        agent_key="research",
        depends_on=[1],
    )
    await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)

    detail = await _detail(client, auth_headers, project["id"])
    failed = _task_by_ordinal(detail, 1)
    blocked = _task_by_ordinal(detail, 2)
    assert failed["status"] == "failed"
    assert failed["attempts"] == failed["max_attempts"]  # bounded retries happened
    assert blocked["status"] == "blocked"
    assert "unrecoverable" in blocked["blocked_reason"]
    assert blocked["run_id"] is None  # the report never started


# ------------------------------------------------------------ Scenario D


async def test_scenario_d_failed_acceptance_bounded_rework(
    client: AsyncClient, auth_headers: dict, db_session
):
    project = await _create_project(
        client, auth_headers, "Rework loop", "Produce something unattainable"
    )
    task = await _add_task(
        client, auth_headers, project["id"],
        title="Investigate topic gamma",
        description="Research gamma, but acceptance demands a field research never emits",
        agent_key="research",
        acceptance_criteria=[
            {
                "description": "output must contain computed metrics",
                "kind": "output_field",
                "config": {"field": "metrics"},
            }
        ],
    )
    await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)

    detail = await _detail(client, auth_headers, project["id"])
    failed = _task_by_ordinal(detail, 1)
    assert failed["status"] == "failed"
    assert "acceptance failed" in failed["error"]
    assert failed["attempts"] == failed["max_attempts"]
    assert failed["revision_feedback"]  # explicit feedback was attached for rework

    reviews = (
        await db_session.scalars(
            select(TaskReview).where(TaskReview.task_id == uuid.UUID(task["id"]))
        )
    ).all()
    assert len(reviews) == failed["max_attempts"]  # every attempt evaluated & preserved
    assert all(not r.passed for r in reviews)
    assert all(
        any("metrics" in str(i) for i in r.issues) for r in reviews
    )

    events = (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=auth_headers)
    ).json()
    types = [e["type"] for e in events]
    assert types.count("task.review_failed") == failed["max_attempts"]
    assert types.count("task.revision_created") == failed["max_attempts"] - 1


# ------------------------------------------------------------ Scenario E


async def test_scenario_e_cyclic_plan_rejected(db_session):
    org, user = await make_org(db_session)
    recorder = ProjectEventRecorder()
    project = await create_project(
        db_session, recorder,
        organization_id=org.id, user_id=user.id,
        name="Cycle test", goal="goal",
    )
    cyclic = PlanDraft(
        goal="cyclic plan",
        tasks=[
            TaskSpec(ref="t1", title="first task", agent="research", depends_on=["t2"]),
            TaskSpec(ref="t2", title="second task", agent="research", depends_on=["t1"]),
        ],
    )
    with pytest.raises(PlanDraftError, match="cycle"):
        await propose_plan(
            db_session, recorder, project=project, user_id=user.id, draft=cyclic
        )
    tasks = (
        await db_session.scalars(
            select(ProjectTask).where(ProjectTask.project_id == project.id)
        )
    ).all()
    assert tasks == []  # nothing was materialized or executed
    plans = (
        await db_session.scalars(
            select(ProjectPlan).where(ProjectPlan.project_id == project.id)
        )
    ).all()
    assert plans == []


# ------------------------------------------------------------ Scenario F


async def test_scenario_f_concurrent_claim_single_winner(db_session):
    org, user = await make_org(db_session)
    recorder = ProjectEventRecorder()
    project = await create_project(
        db_session, recorder,
        organization_id=org.id, user_id=user.id, name="Claims", goal="g",
    )
    task = await create_task(
        db_session, recorder, project=project, user_id=user.id,
        title="claimable task", agent_key="research",
    )
    transition(task, PMTaskStatus.READY)
    await db_session.commit()

    first = await claim_task(db_session, task.id, "worker-one")
    second = await claim_task(db_session, task.id, "worker-two")
    assert first is True
    assert second is False  # the conditional UPDATE admits exactly one claimant

    await db_session.refresh(task)
    assert task.status == PMTaskStatus.ASSIGNED
    assert task.claimed_by == "worker-one"
    assert task.attempts == 1  # a losing claim never bumps attempts


# ------------------------------------------------------------ Scenario G


async def test_scenario_g_unauthorized_access_denied(
    client: AsyncClient, auth_headers: dict
):
    project = await _create_project(
        client, auth_headers, "Private project", "Tenant isolation"
    )
    task = await _add_task(
        client, auth_headers, project["id"],
        title="secret task", agent_key="research",
    )
    other = await register_second_org(client)

    assert (
        await client.get(f"/api/v1/projects/{project['id']}", headers=other)
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            json={"title": "intrusion attempt"},
            headers=other,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/projects/{project['id']}/tasks/{task['id']}/assign",
            json={"agent_key": "research", "mode": "start_now"},
            headers=other,
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/projects/{project['id']}/pm/messages",
            json={"content": "status"},
            headers=other,
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=other)
    ).status_code == 404
    # Nothing changed in the real project.
    detail = await _detail(client, auth_headers, project["id"])
    assert len(detail["tasks"]) == 1


# ------------------------------------------------------------ Scenario H


async def test_scenario_h_worker_crash_recovery(db_session):
    org, user = await make_org(db_session)
    recorder = ProjectEventRecorder()
    project = await create_project(
        db_session, recorder,
        organization_id=org.id, user_id=user.id, name="Recovery", goal="g",
    )
    project.status = "active"
    task = await create_task(
        db_session, recorder, project=project, user_id=user.id,
        title="interrupted task", agent_key="research",
    )
    transition(task, PMTaskStatus.READY)
    await db_session.commit()
    assert await claim_task(db_session, task.id, "doomed-worker")

    # Simulate the crash: the worker started a run and died mid-flight.
    run = AgentRun(
        organization_id=org.id,
        project_id=project.id,
        title="orphan run",
        input_text="x",
        status=RunStatus.RUNNING,
        budgets={"project_task_id": str(task.id)},
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(task)
    task.run_id = run.id
    transition(task, PMTaskStatus.RUNNING)
    task.claimed_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    await db_session.commit()

    recovered = await recover_stale_tasks(ttl_seconds=60)
    assert str(task.id) in recovered

    await db_session.refresh(task)
    await db_session.refresh(run)
    assert task.status == PMTaskStatus.READY  # attempts < max → retryable
    assert task.attempts == 1  # the lost attempt stays counted (no duplicate claim)
    assert "worker lost" in task.error
    assert run.status == RunStatus.FAILED  # orphan run history is truthful

    # Idempotent: a second sweep finds nothing.
    assert await recover_stale_tasks(ttl_seconds=60) == []

    # At the retry limit, recovery fails the task instead of requeueing forever.
    assert await claim_task(db_session, task.id, "doomed-2")
    await db_session.refresh(task)
    transition(task, PMTaskStatus.RUNNING)
    task.attempts = task.max_attempts
    task.claimed_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    await db_session.commit()
    recovered = await recover_stale_tasks(ttl_seconds=60)
    assert str(task.id) in recovered
    await db_session.refresh(task)
    assert task.status == PMTaskStatus.FAILED
