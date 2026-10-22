"""PM plan drafting: LLM structured output when configured, deterministic
templates otherwise. Drafts are proposals — validation and persistence happen
in the service layer; nothing here mutates state.
"""

import re

from app.agents.capabilities import CAPABILITIES, check_assignment
from app.agents.planner import _CRM_HINT, _CSV_HINT, _CUSTOMER_ID
from app.llm.base import LLMProvider
from app.schemas.pm import AcceptanceCriterion, PlanDraft, TaskSpec
from app.services.task_graph import GraphValidationError, validate_dag

PM_PLANNER_SYSTEM = """You are the Project Manager of AgentOS, planning work for
a team of specialized agents. Decompose the owner's goal into the smallest set
of dependency-ordered tasks.

Available assignable agents and the artifact kinds they produce/require:
{capabilities}

Rules:
- Each task gets a unique short ref (t1, t2, …); depends_on lists refs of earlier tasks.
- Assign only the agents listed above. A reviewer task must depend on at least
  one task that produces a deliverable.
- Give each task 1-2 concrete acceptance criteria (kinds: artifact_exists,
  output_field, review_pass).
- Prefer 2-5 tasks. State assumptions and risks explicitly. Never invent
  capabilities or data sources."""


def capabilities_block() -> str:
    lines = []
    for cap in CAPABILITIES.values():
        if not cap.assignable:
            continue
        lines.append(
            f"- {cap.key}: {cap.description[:120]} "
            f"(produces: {', '.join(cap.produced_artifact_kinds) or '—'}; "
            f"requires: {', '.join(cap.required_artifact_kinds) or '—'})"
        )
    return "\n".join(lines)


def deterministic_plan_draft(goal: str) -> PlanDraft:
    """Rule-based decomposition for recognized goal families, always ending in a
    review task so quality control is explicit."""
    tasks: list[TaskSpec] = []
    assumptions: list[str] = []
    customer_match = _CUSTOMER_ID.search(goal)

    if _CRM_HINT.search(goal) and customer_match:
        customer_id = customer_match.group(0)
        tasks = [
            TaskSpec(
                ref="t1",
                title=f"Draft follow-up for {customer_id}",
                description=(
                    f"Retrieve customer {customer_id} and draft a follow-up. {goal}"
                )[:1000],
                agent="executor",
                params={"action": "prepare_followup", "customer_id": customer_id,
                        "context": goal[:400]},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        description="a draft follow-up action exists in the output",
                        kind="output_field",
                        config={"field": "actions"},
                    )
                ],
            ),
            TaskSpec(
                ref="t2",
                title=f"Send approved follow-up to {customer_id}",
                description=(
                    "Send the drafted follow-up via the demo CRM (requires human approval)."
                ),
                agent="executor",
                depends_on=["t1"],
                params={"action": "send_followup", "customer_id": customer_id},
            ),
            TaskSpec(
                ref="t3",
                title="Review CRM workflow outcome",
                description="Verify the follow-up was drafted and delivered with real evidence.",
                agent="reviewer",
                depends_on=["t2"],
            ),
        ]
        assumptions = ["The demo CRM contains the referenced customer."]
    elif _CSV_HINT.search(goal):
        tasks = [
            TaskSpec(
                ref="t1",
                title="Compute dataset metrics",
                description=f"Analyze the available dataset for: {goal}"[:1000],
                agent="analyst",
                params={"last_days": 30},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        description="computed metrics are present",
                        kind="output_field",
                        config={"field": "metrics"},
                    )
                ],
            ),
            TaskSpec(
                ref="t2",
                title="Research context and causes",
                description=f"Ground the analysis in knowledge-base context: {goal}"[:1000],
                agent="research",
                depends_on=["t1"],
            ),
            TaskSpec(
                ref="t3",
                title="Review analysis and findings",
                description="Verify metrics trace to tools and findings cite real sources.",
                agent="reviewer",
                depends_on=["t1", "t2"],
            ),
        ]
        assumptions = ["A ready CSV dataset exists in the knowledge base."]
    else:
        tasks = [
            TaskSpec(
                ref="t1",
                title="Research the question",
                description=goal[:1000],
                agent="research",
            ),
            TaskSpec(
                ref="t2",
                title="Review research findings",
                description="Verify findings are grounded in retrieved sources.",
                agent="reviewer",
                depends_on=["t1"],
            ),
        ]
        assumptions = [
            "Relevant documents exist in the knowledge base (gaps are reported honestly)."
        ]

    return PlanDraft(
        goal=goal[:2000],
        scope="Derived deterministically from the goal (no LLM configured).",
        assumptions=assumptions,
        deliverables=[t.title for t in tasks],
        risks=["Deterministic planning may miss nuances an owner should review."],
        tasks=tasks,
        notes="Plan produced by the deterministic PM planner.",
    )


class PlanDraftError(Exception):
    """Validation failure with a user-facing message."""


def validate_plan_draft(draft: PlanDraft) -> None:
    """Server-side checks: ref uniqueness, task budget, DAG shape, agent
    capability flow. Model output is an untrusted proposal until it passes."""
    from app.core.config import get_settings

    max_tasks = get_settings().max_plan_tasks
    if len(draft.tasks) > max_tasks:
        raise PlanDraftError(
            f"plan proposes {len(draft.tasks)} tasks; the budget allows at most {max_tasks}"
        )
    refs = [t.ref for t in draft.tasks]
    if len(set(refs)) != len(refs):
        raise PlanDraftError("task refs must be unique")
    try:
        check = validate_dag(refs, [(t.ref, dep) for t in draft.tasks for dep in t.depends_on
                                    if not dep.startswith("#")])
    except GraphValidationError as exc:
        raise PlanDraftError(str(exc)) from exc

    by_ref = {t.ref: t for t in draft.tasks}
    produced: dict[str, set[str]] = {}
    for ref in check.order:
        task = by_ref[ref]
        available: set[str] = set()
        for dep in task.depends_on:
            if dep.startswith("#"):
                # Existing-task reference: validated against the live project at
                # materialization time; assume it provides a deliverable.
                available.add("analysis")
                continue
            available |= produced.get(dep, set())
        assignment = check_assignment(task.agent, sorted(available))
        if not assignment.allowed:
            raise PlanDraftError(f"task '{task.title}': {assignment.reason}")
        cap = CAPABILITIES[task.agent]
        produced[ref] = set(cap.produced_artifact_kinds)


async def draft_plan(
    goal: str, llm: LLMProvider | None, feedback: str | None = None
) -> PlanDraft:
    if llm is None:
        return deterministic_plan_draft(goal)
    user = f"Owner goal:\n{goal}"
    if feedback:
        user += f"\n\nYour previous draft failed validation: {feedback}\nFix it."
    draft, _usage = await llm.complete_structured(
        system=PM_PLANNER_SYSTEM.format(capabilities=capabilities_block()),
        user=user,
        schema=PlanDraft,
        max_tokens=3000,
    )
    return draft


def parse_customer_id(text: str) -> str | None:
    match = re.search(r"\bCUST-\d{3,6}\b", text)
    return match.group(0) if match else None
