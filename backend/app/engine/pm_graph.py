"""PM planning workflow (LangGraph): draft → validate → bounded redraft → done.

The graph produces a validated PlanDraft; persistence (plan version rows, task
materialization) stays in the service layer. Validation is deterministic code —
an invalid LLM draft gets exactly one corrective redraft before failing.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.pm import PlanDraftError, deterministic_plan_draft, draft_plan, validate_plan_draft
from app.llm.base import LLMError, LLMProvider
from app.schemas.pm import PlanDraft

MAX_DRAFT_ATTEMPTS = 2


class PMPlanState(TypedDict, total=False):
    goal: str
    draft: dict
    error: str
    attempts: int
    valid: bool


def build_pm_plan_graph(llm: LLMProvider | None):
    async def draft_node(state: PMPlanState) -> PMPlanState:
        attempts = state.get("attempts", 0) + 1
        try:
            draft = await draft_plan(state["goal"], llm, feedback=state.get("error"))
        except LLMError:
            draft = deterministic_plan_draft(state["goal"])  # degrade honestly
        return {"draft": draft.model_dump(mode="json"), "attempts": attempts, "error": ""}

    async def validate_node(state: PMPlanState) -> PMPlanState:
        try:
            validate_plan_draft(PlanDraft.model_validate(state["draft"]))
        except (PlanDraftError, ValueError) as exc:
            return {"valid": False, "error": str(exc)}
        return {"valid": True, "error": ""}

    def route(state: PMPlanState) -> str:
        if state.get("valid"):
            return "done"
        if state.get("attempts", 0) < MAX_DRAFT_ATTEMPTS:
            return "redraft"
        return "give_up"

    graph = StateGraph(PMPlanState)
    graph.add_node("draft", draft_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "validate")
    graph.add_conditional_edges(
        "validate", route, {"done": END, "redraft": "draft", "give_up": END}
    )
    return graph.compile()


async def generate_validated_plan(goal: str, llm: LLMProvider | None) -> PlanDraft:
    """Run the planning graph; raises PlanDraftError if no valid draft emerged."""
    graph = build_pm_plan_graph(llm)
    result = await graph.ainvoke({"goal": goal})
    if not result.get("valid"):
        raise PlanDraftError(result.get("error") or "plan drafting failed")
    return PlanDraft.model_validate(result["draft"])
