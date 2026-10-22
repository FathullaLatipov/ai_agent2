"""Built-in system agent definitions — the source of truth for tool allowlists.

Synced to the agent_definitions table at startup/seed so the UI can display them
and the registry enforces allowlists from persistent state.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemAgentSpec:
    key: str
    name: str
    description: str
    allowed_tools: tuple[str, ...] = ()
    max_iterations: int = 10
    timeout_s: int = 180


SYSTEM_AGENTS: dict[str, SystemAgentSpec] = {
    spec.key: spec
    for spec in (
        SystemAgentSpec(
            key="planner",
            name="Planner",
            description=(
                "Converts natural-language requests into a validated, dependency-ordered "
                "task plan with execution budgets. Calls no tools."
            ),
            allowed_tools=(),
        ),
        SystemAgentSpec(
            key="research",
            name="Research Agent",
            description=(
                "Searches the organization's knowledge base and returns findings grounded "
                "in retrieved sources. Distinguishes evidence from assumptions; never "
                "invents citations."
            ),
            allowed_tools=("kb.search", "kb.get_document"),
        ),
        SystemAgentSpec(
            key="analyst",
            name="Data Analyst",
            description=(
                "Inspects CSV datasets and computes metrics with deterministic tools. "
                "All arithmetic happens in code, never in the model."
            ),
            allowed_tools=(
                "dataset.inspect",
                "dataset.stats",
                "dataset.aggregate",
                "calculator.evaluate",
                "sql.read_query",
            ),
        ),
        SystemAgentSpec(
            key="executor",
            name="Executor",
            description=(
                "Calls registered business tools with validated arguments. High-impact "
                "operations require human approval enforced server-side."
            ),
            allowed_tools=(
                "crm.list_customers",
                "crm.get_customer",
                "crm.draft_followup",
                "crm.send_followup",
                "calculator.evaluate",
            ),
        ),
        SystemAgentSpec(
            key="reviewer",
            name="Reviewer",
            description=(
                "Checks factual consistency, recomputes arithmetic, validates that "
                "conclusions trace to evidence, and requests bounded revisions."
            ),
            allowed_tools=("calculator.evaluate",),
        ),
        SystemAgentSpec(
            key="reporter",
            name="Report Agent",
            description=(
                "Assembles the final structured report (executive summary, methodology, "
                "evidence, findings, recommendations, limitations) and persists it."
            ),
            allowed_tools=("report.render",),
        ),
    )
}
