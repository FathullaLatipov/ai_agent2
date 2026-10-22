"""Structured agent capability metadata + matching.

The authority for "can this agent do this task" — assignment validation uses
this registry, never free-form descriptions or model claims. Built on top of
the existing SYSTEM_AGENTS specs (tool allowlists stay in agent_definitions).
"""

from dataclasses import dataclass, field

from app.agents.definitions import SYSTEM_AGENTS

# Artifact kinds produced/consumed by agents. "deliverable" is the generic
# review subject; producers declare the concrete kind they emit.
DELIVERABLE_KINDS = {"analysis", "findings", "action_result", "document"}


@dataclass(frozen=True)
class AgentCapability:
    key: str
    name: str
    description: str
    task_kinds: tuple[str, ...]  # what work it does (matching hints)
    required_artifact_kinds: tuple[str, ...] = ()  # must exist among inputs
    produced_artifact_kinds: tuple[str, ...] = ()
    assignable: bool = True  # directly assignable to a project task
    unassignable_reason: str = ""
    timeout_s: int = 180
    max_attempts: int = 3


CAPABILITIES: dict[str, AgentCapability] = {
    cap.key: cap
    for cap in (
        AgentCapability(
            key="planner",
            name="Planner",
            description=SYSTEM_AGENTS["planner"].description,
            task_kinds=("planning",),
            assignable=False,
            unassignable_reason=(
                "project planning is performed by the PM itself; ask the PM to "
                "'create a plan' instead of assigning the planner to a task"
            ),
        ),
        AgentCapability(
            key="research",
            name="Research Agent",
            description=SYSTEM_AGENTS["research"].description,
            task_kinds=("research", "question", "investigation"),
            produced_artifact_kinds=("findings",),
        ),
        AgentCapability(
            key="analyst",
            name="Data Analyst",
            description=SYSTEM_AGENTS["analyst"].description,
            task_kinds=("analysis", "metrics", "dataset"),
            produced_artifact_kinds=("analysis",),
        ),
        AgentCapability(
            key="executor",
            name="Executor",
            description=SYSTEM_AGENTS["executor"].description,
            task_kinds=("action", "crm", "workflow"),
            produced_artifact_kinds=("action_result",),
        ),
        AgentCapability(
            key="reviewer",
            name="Reviewer",
            description=SYSTEM_AGENTS["reviewer"].description,
            task_kinds=("review", "validation", "qa"),
            required_artifact_kinds=("deliverable",),
            produced_artifact_kinds=("review",),
        ),
        AgentCapability(
            key="reporter",
            name="Report Agent",
            description=SYSTEM_AGENTS["reporter"].description,
            task_kinds=("reporting",),
            assignable=False,
            unassignable_reason=(
                "a report is produced automatically for every executed attempt; "
                "use a research/analyst task for standalone written deliverables"
            ),
        ),
    )
}


def get_capability(agent_key: str) -> AgentCapability | None:
    return CAPABILITIES.get(agent_key)


@dataclass
class AssignmentCheck:
    allowed: bool
    missing: list[str] = field(default_factory=list)  # unmet prerequisites, user-facing
    reason: str = ""


def artifact_satisfies(kind_required: str, kind_present: str) -> bool:
    if kind_required == "deliverable":
        return kind_present in DELIVERABLE_KINDS
    return kind_required == kind_present


def check_assignment(
    agent_key: str, available_artifact_kinds: list[str]
) -> AssignmentCheck:
    """Deterministic validation: does this agent exist, is it assignable, and are
    its required input artifacts available (from dependencies or supplied)?"""
    capability = CAPABILITIES.get(agent_key)
    if capability is None:
        return AssignmentCheck(
            allowed=False, reason=f"no registered agent with key '{agent_key}'"
        )
    if not capability.assignable:
        return AssignmentCheck(allowed=False, reason=capability.unassignable_reason)
    missing = [
        required
        for required in capability.required_artifact_kinds
        if not any(artifact_satisfies(required, present) for present in available_artifact_kinds)
    ]
    if missing:
        return AssignmentCheck(
            allowed=False,
            missing=missing,
            reason=(
                f"{capability.name} requires input artifacts of kind "
                f"{', '.join(missing)}, and none are available yet"
            ),
        )
    return AssignmentCheck(allowed=True)


def producers_for(artifact_kind: str) -> list[AgentCapability]:
    """Assignable agents that can produce the given artifact kind."""
    return [
        cap
        for cap in CAPABILITIES.values()
        if cap.assignable
        and any(artifact_satisfies(artifact_kind, kind) for kind in cap.produced_artifact_kinds)
    ]


def suggest_agent_for(description: str) -> str:
    """Deterministic agent suggestion from task text (reuses planner heuristics)."""
    from app.agents.planner import _CRM_HINT, _CSV_HINT

    if _CRM_HINT.search(description):
        return "executor"
    if _CSV_HINT.search(description):
        return "analyst"
    lowered = description.lower()
    if any(word in lowered for word in ("review", "validate", "verify", "qa ")):
        return "reviewer"
    return "research"
