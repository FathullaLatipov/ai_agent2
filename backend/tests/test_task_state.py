"""Task state machine: legal paths succeed, illegal transitions raise."""

import pytest

from app.models.pm import PMTaskStatus, ProjectTask
from app.services.task_state import IllegalTransitionError, transition


def _task(status: PMTaskStatus) -> ProjectTask:
    return ProjectTask(ordinal=1, title="t", status=status)


def test_happy_path_sequence():
    task = _task(PMTaskStatus.DRAFT)
    for target in (
        PMTaskStatus.PLANNED,
        PMTaskStatus.BLOCKED,
        PMTaskStatus.READY,
        PMTaskStatus.ASSIGNED,
        PMTaskStatus.RUNNING,
        PMTaskStatus.REVIEWING,
        PMTaskStatus.SUCCEEDED,
    ):
        transition(task, target)
    assert task.status == PMTaskStatus.SUCCEEDED
    assert task.started_at is not None
    assert task.finished_at is not None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (PMTaskStatus.BLOCKED, PMTaskStatus.RUNNING),  # cannot skip ready/assigned
        (PMTaskStatus.DRAFT, PMTaskStatus.RUNNING),
        (PMTaskStatus.SUCCEEDED, PMTaskStatus.READY),  # terminal
        (PMTaskStatus.CANCELLED, PMTaskStatus.READY),  # terminal
        (PMTaskStatus.READY, PMTaskStatus.SUCCEEDED),  # cannot succeed without running
        (PMTaskStatus.RUNNING, PMTaskStatus.SUCCEEDED),  # must pass reviewing
    ],
)
def test_illegal_transitions_raise(source, target):
    with pytest.raises(IllegalTransitionError):
        transition(_task(source), target)


def test_ready_transition_clears_claim_and_block_reason():
    task = _task(PMTaskStatus.NEEDS_REVISION)
    task.claimed_by = "w1"
    task.blocked_reason = "old"
    transition(task, PMTaskStatus.READY)
    assert task.claimed_by is None
    assert task.blocked_reason is None


def test_rework_path():
    task = _task(PMTaskStatus.REVIEWING)
    transition(task, PMTaskStatus.NEEDS_REVISION)
    transition(task, PMTaskStatus.READY)
    assert task.status == PMTaskStatus.READY


def test_approval_resume_path():
    task = _task(PMTaskStatus.RUNNING)
    transition(task, PMTaskStatus.WAITING_APPROVAL)
    transition(task, PMTaskStatus.RUNNING)
    transition(task, PMTaskStatus.REVIEWING)
    assert task.status == PMTaskStatus.REVIEWING
