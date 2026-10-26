"""PM conversation commands and the end-to-end CRM project with a human
approval gate in the middle (deterministic mode throughout)."""

from httpx import AsyncClient

from tests.test_pm_harness import _add_task, _create_project, _detail, _task_by_ordinal


async def _pm(client: AsyncClient, headers: dict, project_id: str, content: str) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/pm/messages",
        json={"content": content},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_pm_sales_project_with_dataset_autodetect(
    client: AsyncClient, auth_headers: dict, db_session, tmp_path
):
    """PM-planned sales project: the deterministic PM draft assigns the analyst
    without a dataset id, and the analyst auto-detects the org's ready CSV
    (gap found during the VPS staging smoke)."""
    import uuid as uuid_mod

    from tests.helpers import seed_csv_document, write_sales_csv

    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    org_id = me.json()["memberships"][0]["organization"]["id"]
    csv_path = tmp_path / "sales.csv"
    write_sales_csv(csv_path)
    await seed_csv_document(db_session, uuid_mod.UUID(org_id), csv_path)
    await db_session.commit()

    project = await _create_project(
        client, auth_headers, "Sales via PM",
        "Analyze the last 30 days of sales and identify underperforming products",
    )
    resp = await client.post(f"/api/v1/projects/{project['id']}/plan", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["tasks_spec"][0]["agent"] == "analyst"
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    detail = await _detail(client, auth_headers, project["id"])
    states = {t["ordinal"]: (t["status"], t["error"]) for t in detail["tasks"]}
    assert detail["status"] == "completed", states
    assert all(t["status"] == "succeeded" for t in detail["tasks"]), states
    analyst = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks/{_task_by_ordinal(detail, 1)['id']}",
            headers=auth_headers,
        )
    ).json()
    assert "Underperforming" in analyst["result"]["output"]["summary"]


async def test_pm_conversation_commands(client: AsyncClient, auth_headers: dict):
    project = await _create_project(
        client, auth_headers, "Chat ops", "Answer questions about our policies"
    )

    # Plan creation requires approval — the PM says so and does not execute.
    reply = await _pm(client, auth_headers, project["id"], "Create a plan for this project")
    assert reply["role"] == "pm"
    assert reply["meta"]["intent"]["kind"] == "create_plan"
    assert "approval" in reply["content"].lower() or "approve" in reply["content"].lower()
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "awaiting_approval"
    assert detail["tasks"] == []  # proposed, not materialized

    # Running before approval is refused with an explanation.
    reply = await _pm(client, auth_headers, project["id"], "run all ready tasks")
    assert "approv" in reply["content"].lower()
    assert _task_count(detail) == 0

    # Status reporting reflects persisted state only.
    reply = await _pm(client, auth_headers, project["id"], "show me the status")
    assert reply["meta"]["intent"]["kind"] == "status_report"
    assert "awaiting_approval" in reply["content"]

    # Blocked explanation for a concrete task.
    await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed"  # deterministic plan ran to completion

    reply = await _pm(client, auth_headers, project["id"], "why is #2 blocked?")
    assert reply["meta"]["intent"]["kind"] == "explain_blocked"
    assert "not blocked" in reply["content"]

    # Unknown commands get an honest help answer, not a fake success.
    reply = await _pm(client, auth_headers, project["id"], "make me a sandwich")
    assert "didn't recognize" in reply["content"]

    # Conversation history is persisted and scoped to the project.
    history = (
        await client.get(f"/api/v1/projects/{project['id']}/pm/messages", headers=auth_headers)
    ).json()
    roles = [m["role"] for m in history]
    assert roles.count("owner") == 5
    assert roles.count("pm") == 5


def _task_count(detail: dict) -> int:
    return len(detail["tasks"])


async def test_pm_assign_via_chat(client: AsyncClient, auth_headers: dict):
    project = await _create_project(
        client, auth_headers, "Chat assign", "Investigate a topic"
    )
    await _add_task(
        client, auth_headers, project["id"],
        title="Look into topic delta", description="Investigate delta",
    )
    reply = await _pm(client, auth_headers, project["id"], 'assign #1 to research')
    assert reply["meta"]["intent"]["kind"] == "assign_task"
    assert reply["meta"]["intent"]["agent_key"] == "research"
    detail = await _detail(client, auth_headers, project["id"])
    task = _task_by_ordinal(detail, 1)
    assert task["agent_key"] == "research"
    assert task["requested_agent_key"] == "research"
    assert "assigned" in reply["content"].lower()

    reply = await _pm(client, auth_headers, project["id"], "start research on #1 now")
    assert "started" in reply["content"].lower()
    detail = await _detail(client, auth_headers, project["id"])
    assert _task_by_ordinal(detail, 1)["status"] == "succeeded"


async def test_full_crm_project_with_tool_approval_gate(
    client: AsyncClient, auth_headers: dict
):
    """Showcase: PM plan → approval → execute → tool-level human approval pause →
    approve → resume → review → project completed. All states persisted."""
    project = await _create_project(
        client,
        auth_headers,
        "Customer follow-up",
        "Prepare a customer follow-up for CUST-1001 about the renewal and send it",
    )
    reply = await _pm(client, auth_headers, project["id"], "create a plan")
    assert "t2" in reply["content"] or "3 task" in reply["content"]

    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Execution pauses at the high-impact send: task #2 waits for human approval.
    detail = await _detail(client, auth_headers, project["id"])
    assert _task_by_ordinal(detail, 1)["status"] == "succeeded"  # draft prepared
    assert _task_by_ordinal(detail, 2)["status"] == "waiting_approval"
    assert _task_by_ordinal(detail, 3)["status"] == "blocked"  # review waits

    # The PM explains the blocker truthfully.
    reply = await _pm(client, auth_headers, project["id"], "why is #3 blocked?")
    assert "waiting" in reply["content"].lower()

    approvals = (
        await client.get("/api/v1/approvals?status=pending", headers=auth_headers)
    ).json()
    assert len(approvals) == 1
    assert approvals[0]["tool_name"] == "crm.send_followup"
    decided = await client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decide",
        json={"decision": "approve", "reason": "pm harness e2e"},
        headers=auth_headers,
    )
    assert decided.status_code == 200

    detail = await _detail(client, auth_headers, project["id"])
    task_states = {t["ordinal"]: (t["status"], t["error"]) for t in detail["tasks"]}
    assert detail["status"] == "completed", task_states
    assert all(t["status"] == "succeeded" for t in detail["tasks"]), task_states
    # The reviewer's verdict is persisted in the task result.
    review_task = _task_by_ordinal(detail, 3)
    assert review_task["agent_key"] == "reviewer"

    events = (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=auth_headers)
    ).json()
    types = [e["type"] for e in events]
    for expected in (
        "plan.proposed",
        "plan.approved",
        "task.started",
        "task.waiting_approval",
        "task.succeeded",
        "project.completed",
    ):
        assert expected in types, f"missing event {expected}"


async def test_schedule_blocked_until_plan_approved(
    client: AsyncClient, auth_headers: dict
):
    project = await _create_project(
        client, auth_headers, "Gate", "Investigate something"
    )
    resp = await client.post(f"/api/v1/projects/{project['id']}/plan", headers=auth_headers)
    assert resp.status_code == 200
    # Project is awaiting approval → scheduling is refused server-side.
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers
    )
    assert resp.status_code == 409
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["tasks"] == []
