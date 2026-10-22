"""DAG validation and readiness (deterministic, no DB)."""

import pytest

from app.models.pm import PMTaskStatus
from app.services.task_graph import (
    GraphValidationError,
    compute_readiness,
    validate_dag,
)


def test_valid_dag_returns_topological_order():
    check = validate_dag(
        ["a", "b", "c", "d"],
        [("c", "a"), ("c", "b"), ("d", "c")],
    )
    assert check.order.index("a") < check.order.index("c")
    assert check.order.index("b") < check.order.index("c")
    assert check.order.index("c") < check.order.index("d")


def test_cycle_rejected():
    with pytest.raises(GraphValidationError, match="cycle"):
        validate_dag(["a", "b", "c"], [("b", "a"), ("c", "b"), ("a", "c")])


def test_two_node_cycle_rejected():
    with pytest.raises(GraphValidationError, match="cycle"):
        validate_dag(["a", "b"], [("a", "b"), ("b", "a")])


def test_self_dependency_rejected():
    with pytest.raises(GraphValidationError, match="depend on itself"):
        validate_dag(["a"], [("a", "a")])


def test_unknown_reference_rejected():
    with pytest.raises(GraphValidationError, match="unknown task"):
        validate_dag(["a"], [("a", "ghost")])


def test_duplicate_ids_rejected():
    with pytest.raises(GraphValidationError, match="duplicate"):
        validate_dag(["a", "a"], [])


def test_readiness_requires_all_succeeded():
    ready = compute_readiness(
        [("1", PMTaskStatus.SUCCEEDED, "one"), ("2", PMTaskStatus.SUCCEEDED, "two")]
    )
    assert ready.ready

    waiting = compute_readiness(
        [("1", PMTaskStatus.SUCCEEDED, "one"), ("2", PMTaskStatus.RUNNING, "two")]
    )
    assert not waiting.ready
    assert "'two' is running" in waiting.blocked_reason


def test_failed_dependency_blocks_permanently():
    blocked = compute_readiness([("1", PMTaskStatus.FAILED, "analysis")])
    assert not blocked.ready
    assert "unrecoverable" in blocked.blocked_reason
    assert "analysis" in blocked.blocked_reason
