"""LLM planning path for the PM harness.

Deterministic tests use FakeLLMProvider — model output is treated as an
untrusted proposal and must survive server-side validation (capabilities, DAG,
budgets) and the human approval gate. The live-provider test is opt-in and
skips honestly when no credentials are configured.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.agents.pm import PlanDraftError
from app.core.config import get_settings
from app.engine.pm_graph import generate_validated_plan
from app.llm.base import LLMTransientError, LLMUsage
from app.llm.factory import set_provider_override
from app.llm.fake import FakeLLMProvider
from app.models import ProjectPlan, ProjectTask
from app.services.pm import propose_plan
from app.services.project_events import ProjectEventRecorder
from app.services.projects import create_project
from tests.helpers import make_org
from tests.test_pm_harness import _create_project, _detail, _task_by_ordinal


@pytest.fixture
def fake_llm():
    provider = FakeLLMProvider()
    set_provider_override(provider)
    yield provider
    set_provider_override(None)


def _task(ref: str, agent: str, depends_on: list[str] | None = None, **extra) -> dict:
    return {
        "ref": ref,
        "title": extra.pop("title", f"Task {ref} does something"),
        "description": f"work for {ref}",
        "agent": agent,
        "depends_on": depends_on or [],
        **extra,
    }


def _plan(tasks: list[dict], goal: str = "a realistic multi-step goal") -> dict:
    return {"goal": goal, "tasks": tasks, "notes": "llm draft"}


VALID_PLAN = _plan(
    [
        _task("t1", "research"),
        _task("t2", "research"),
        _task("t3", "reviewer", ["t1", "t2"]),
    ]
)


# ------------------------------------------------- draft/validate behavior


async def test_llm_plan_accepted_when_valid(fake_llm):
    fake_llm.enqueue(VALID_PLAN)
    draft = await generate_validated_plan("investigate two topics and review", fake_llm)
    assert [t.agent for t in draft.tasks] == ["research", "research", "reviewer"]
    assert len(fake_llm.calls) == 1
    # The planning prompt carries the real capability registry, not free text only.
    assert "produces:" in fake_llm.calls[0]["system"]


async def test_malformed_output_falls_back_deterministic_with_annotation(fake_llm):
    fake_llm.enqueue({"garbage": True})  # fails PlanDraft schema → LLMError
    draft = await generate_validated_plan("What is our refund policy?", fake_llm)
    assert "deterministic fallback used" in draft.notes
    assert "schema validation" in draft.notes  # the reason is recorded, no secrets
    assert draft.tasks  # the fallback is a real, valid plan


async def test_unknown_agent_triggers_one_corrective_redraft(fake_llm):
    fake_llm.enqueue(_plan([_task("t1", "hacker-agent")]))
    fake_llm.enqueue(VALID_PLAN)
    draft = await generate_validated_plan("goal", fake_llm)
    assert len(fake_llm.calls) == 2
    # The redraft prompt contains the validation feedback.
    assert "no registered agent" in fake_llm.calls[1]["user"]
    assert len(draft.tasks) == 3


async def test_unassignable_agent_rejected(fake_llm):
    fake_llm.enqueue(_plan([_task("t1", "planner")]))
    fake_llm.enqueue(_plan([_task("t1", "reporter")]))
    with pytest.raises(PlanDraftError, match=r"PM itself|automatically"):
        await generate_validated_plan("goal", fake_llm)


async def test_invalid_dependency_reference_rejected(fake_llm):
    bad = _plan([_task("t1", "research", ["t9"])])
    fake_llm.enqueue(bad)
    fake_llm.enqueue(bad)
    with pytest.raises(PlanDraftError, match="unknown task"):
        await generate_validated_plan("goal", fake_llm)


async def test_cyclic_llm_plan_rejected_and_never_persisted(fake_llm, db_session):
    cyclic = _plan(
        [_task("t1", "research", ["t2"]), _task("t2", "research", ["t1"])]
    )
    fake_llm.enqueue(cyclic)
    fake_llm.enqueue(cyclic)

    org, user = await make_org(db_session)
    recorder = ProjectEventRecorder()
    project = await create_project(
        db_session, recorder, organization_id=org.id, user_id=user.id,
        name="Cycle via LLM", goal="goal",
    )
    with pytest.raises(PlanDraftError, match="cycle"):
        await propose_plan(db_session, recorder, project=project, user_id=user.id)
    assert (
        await db_session.scalars(
            select(ProjectPlan).where(ProjectPlan.project_id == project.id)
        )
    ).all() == []
    assert (
        await db_session.scalars(
            select(ProjectTask).where(ProjectTask.project_id == project.id)
        )
    ).all() == []


async def test_reviewer_without_deliverable_producer_rejected(fake_llm):
    # Reviewer with no upstream producer: capability flow check must refuse.
    bad = _plan([_task("t1", "reviewer")])
    fake_llm.enqueue(bad)
    fake_llm.enqueue(bad)
    with pytest.raises(PlanDraftError, match="requires input artifacts"):
        await generate_validated_plan("goal", fake_llm)


async def test_plan_exceeding_task_budget_rejected(fake_llm):
    too_many = _plan(
        [_task(f"t{i}", "research") for i in range(get_settings().max_plan_tasks + 1)]
    )
    fake_llm.enqueue(too_many)
    fake_llm.enqueue(too_many)
    with pytest.raises(PlanDraftError, match="budget allows at most"):
        await generate_validated_plan("goal", fake_llm)


async def test_provider_timeout_falls_back_with_annotation():
    class TimeoutProvider:
        name = "timeout"

        async def complete_structured(self, *, system, user, schema, max_tokens=2048):
            raise LLMTransientError("request timed out after 60s")

    draft = await generate_validated_plan("What is our refund policy?", TimeoutProvider())
    assert "deterministic fallback used" in draft.notes
    assert "timed out" in draft.notes


def test_fake_provider_reports_usage_shape():
    # Usage is reported per the provider contract (never fabricated elsewhere).
    assert LLMUsage(input_tokens=None, output_tokens=None).input_tokens is None


# -------------------------------------------- end-to-end with approval gate


async def test_llm_generated_plan_full_lifecycle(
    client: AsyncClient, auth_headers: dict, fake_llm
):
    """User request → LLM plan → server validation → human approval →
    dependency-aware execution → review → completed. The LLM proposes only;
    every enforcement stays backend-side."""
    fake_llm.enqueue(
        _plan(
            [
                _task("t1", "research", title="Investigate topic alpha"),
                _task("t2", "research", title="Investigate topic beta"),
                _task("t3", "reviewer", ["t1", "t2"], title="Review both investigations"),
            ],
            goal="Investigate two topics and review the findings",
        )
    )
    project = await _create_project(
        client, auth_headers, "LLM planned", "Investigate two topics and review"
    )
    resp = await client.post(f"/api/v1/projects/{project['id']}/plan", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "proposed"
    assert len(fake_llm.calls) == 1  # the plan really came from the provider

    # Untrusted proposal: nothing materialized, nothing runs before approval.
    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "awaiting_approval"
    assert detail["tasks"] == []
    assert (
        await client.post(f"/api/v1/projects/{project['id']}/schedule", headers=auth_headers)
    ).status_code == 409

    # Execution itself runs deterministically (no queued agent payloads).
    set_provider_override(None)
    resp = await client.post(
        f"/api/v1/projects/{project['id']}/plans/1/decide",
        json={"approve": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    detail = await _detail(client, auth_headers, project["id"])
    assert detail["status"] == "completed", detail["task_counts"]
    assert all(t["status"] == "succeeded" for t in detail["tasks"])
    assert _task_by_ordinal(detail, 3)["depends_on"] == [1, 2]

    events = (
        await client.get(f"/api/v1/projects/{project['id']}/events", headers=auth_headers)
    ).json()
    done = {e["payload"]["ordinal"]: e["seq"] for e in events if e["type"] == "task.succeeded"}
    review_start = next(
        e["seq"] for e in events if e["type"] == "task.started" and e["payload"]["ordinal"] == 3
    )
    assert review_start > done[1] and review_start > done[2]


# ------------------------------------------------------- live provider (opt-in)


@pytest.mark.live_llm
@pytest.mark.skipif(
    not get_settings().anthropic_api_key,
    reason="requires AGENTOS_ANTHROPIC_API_KEY (live Anthropic API access)",
)
async def test_live_anthropic_plan_generation():
    """Opt-in: real Anthropic call. Verifies the live provider produces a plan
    that passes the same server-side validation as any other proposal."""
    from app.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider()
    draft = await generate_validated_plan(
        "Analyze the last 30 days of sales, identify underperforming products, "
        "and review the findings",
        provider,
    )
    assert 1 <= len(draft.tasks) <= get_settings().max_plan_tasks
    assert all(t.agent in ("research", "analyst", "executor", "reviewer") for t in draft.tasks)
