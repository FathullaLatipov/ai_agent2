"""Deterministic planner templates and plan structure validation."""

import pytest

from app.agents.planner import deterministic_plan
from app.core.config import get_settings
from app.schemas.agents import PlannerOutput, PlanTask

SETTINGS = get_settings()

CSV_DOC = {
    "id": "7e2a1a7e-0000-4000-8000-000000000001",
    "filename": "sales.csv",
    "content_type": "text/csv",
    "status": "ready",
}


def test_sales_analysis_plan():
    plan = deterministic_plan(
        "Analyze the last 30 days of sales and find underperforming products",
        [CSV_DOC],
        {},
        SETTINGS,
    )
    assert plan.tasks[0].agent == "analyst"
    assert plan.tasks[0].params["dataset_document_id"] == CSV_DOC["id"]
    assert plan.tasks[0].params["last_days"] == 30
    assert plan.tasks[1].agent == "research"
    assert plan.tasks[1].depends_on == [0]


def test_crm_followup_plan():
    plan = deterministic_plan(
        "Prepare a customer follow-up for CUST-1001 and send it", [], {}, SETTINGS
    )
    assert [t.agent for t in plan.tasks] == ["executor", "executor"]
    assert plan.tasks[0].params["action"] == "prepare_followup"
    assert plan.tasks[1].params["action"] == "send_followup"
    assert plan.tasks[1].depends_on == [0]
    assert plan.tasks[0].params["customer_id"] == "CUST-1001"


def test_kb_question_fallback_plan():
    plan = deterministic_plan("What is our refund policy?", [], {}, SETTINGS)
    assert len(plan.tasks) == 1
    assert plan.tasks[0].agent == "research"


def test_plan_structure_rejects_gaps_and_forward_deps():
    plan = PlannerOutput(
        goal="bad plan",
        tasks=[
            PlanTask(ordinal=0, agent="research", objective="first task", depends_on=[1]),
            PlanTask(ordinal=1, agent="research", objective="second task"),
        ],
    )
    with pytest.raises(ValueError, match="depends on"):
        plan.validate_structure(8)

    plan2 = PlannerOutput(
        goal="gap plan",
        tasks=[PlanTask(ordinal=2, agent="research", objective="gapped task")],
    )
    with pytest.raises(ValueError, match="ordinals"):
        plan2.validate_structure(8)


def test_plan_budget_enforced():
    plan = PlannerOutput(
        goal="too many",
        tasks=[
            PlanTask(ordinal=i, agent="research", objective=f"task {i}") for i in range(5)
        ],
    )
    with pytest.raises(ValueError, match="budget"):
        plan.validate_structure(3)
