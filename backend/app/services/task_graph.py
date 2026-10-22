"""Dependency-graph validation and readiness computation.

Pure functions over task/edge data — deterministic, no LLM involvement. The
scheduler and plan validation both rely on this module; model output is never
the authority on eligibility.
"""

from dataclasses import dataclass, field

from app.models.pm import PMTaskStatus


class GraphValidationError(Exception):
    """Human-readable DAG validation failure."""


@dataclass
class GraphCheck:
    order: list[str] = field(default_factory=list)  # topological order of task ids


def validate_dag(task_ids: list[str], edges: list[tuple[str, str]]) -> GraphCheck:
    """Validate (task → depends_on) edges: unknown refs, self-deps, cycles.

    Raises GraphValidationError with a precise message; returns a topological
    order on success (dependencies before dependents).
    """
    known = set(task_ids)
    if len(known) != len(task_ids):
        raise GraphValidationError("duplicate task ids in graph")
    adjacency: dict[str, set[str]] = {tid: set() for tid in task_ids}
    indegree: dict[str, int] = {tid: 0 for tid in task_ids}

    for task_id, depends_on in edges:
        if task_id not in known:
            raise GraphValidationError(f"dependency references unknown task {task_id}")
        if depends_on not in known:
            raise GraphValidationError(
                f"task {task_id} depends on unknown task {depends_on}"
            )
        if task_id == depends_on:
            raise GraphValidationError(f"task {task_id} cannot depend on itself")
        if task_id in adjacency[depends_on]:
            continue  # duplicate edge — harmless
        adjacency[depends_on].add(task_id)
        indegree[task_id] += 1

    # Kahn's algorithm: anything left over sits on a cycle.
    queue = sorted(tid for tid, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dependent in sorted(adjacency[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if len(order) != len(task_ids):
        cyclic = sorted(tid for tid, deg in indegree.items() if deg > 0)
        raise GraphValidationError(
            f"dependency cycle detected involving tasks: {', '.join(cyclic)}"
        )
    return GraphCheck(order=order)


@dataclass
class Readiness:
    ready: bool
    blocked_reason: str | None = None


def compute_readiness(
    dep_statuses: list[tuple[str, PMTaskStatus, str]],
) -> Readiness:
    """Readiness of one task from its dependencies' (id, status, title).

    Eligible only when every dependency has SUCCEEDED. A failed/cancelled
    dependency keeps the task blocked with an explicit, user-facing reason.
    """
    unmet: list[str] = []
    dead: list[str] = []
    for _dep_id, status, title in dep_statuses:
        if status == PMTaskStatus.SUCCEEDED:
            continue
        if status in (PMTaskStatus.FAILED, PMTaskStatus.CANCELLED):
            dead.append(f"'{title}' {status.value}")
        else:
            unmet.append(f"'{title}' is {status.value}")
    if dead:
        return Readiness(
            ready=False,
            blocked_reason="blocked by unrecoverable dependencies: " + "; ".join(dead),
        )
    if unmet:
        return Readiness(
            ready=False, blocked_reason="waiting for dependencies: " + "; ".join(unmet)
        )
    return Readiness(ready=True)
