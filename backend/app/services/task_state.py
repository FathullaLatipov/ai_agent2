"""Project-task state machine. Every status mutation goes through transition().

Transition table (see docs/pm-harness.md §2):

    draft    → planned | cancelled
    planned  → blocked | ready | cancelled
    blocked  → ready | blocked | cancelled           (recomputation may re-block)
    ready    → assigned | blocked | waiting_approval | cancelled
    waiting_approval → ready | running | cancelled | failed
                       (→running = approval granted, execution resumes)
    assigned → running | ready | failed | cancelled  (→ready = claim released/recovered)
    running  → reviewing | waiting_approval | failed | cancelled | ready(recovery)
    reviewing → succeeded | needs_revision | failed
    needs_revision → ready | failed | cancelled      (bounded rework)
    failed   → ready | cancelled                     (manual retry)
    succeeded, cancelled → terminal
"""

import datetime as dt

from app.models.pm import PMTaskStatus, ProjectTask

_T = PMTaskStatus

ALLOWED_TRANSITIONS: dict[PMTaskStatus, set[PMTaskStatus]] = {
    _T.DRAFT: {_T.PLANNED, _T.CANCELLED},
    _T.PLANNED: {_T.BLOCKED, _T.READY, _T.CANCELLED},
    _T.BLOCKED: {_T.READY, _T.BLOCKED, _T.CANCELLED},
    _T.READY: {_T.ASSIGNED, _T.BLOCKED, _T.WAITING_APPROVAL, _T.CANCELLED},
    _T.WAITING_APPROVAL: {_T.READY, _T.RUNNING, _T.CANCELLED, _T.FAILED},
    _T.ASSIGNED: {_T.RUNNING, _T.READY, _T.FAILED, _T.CANCELLED},
    _T.RUNNING: {_T.REVIEWING, _T.WAITING_APPROVAL, _T.FAILED, _T.CANCELLED, _T.READY},
    _T.REVIEWING: {_T.SUCCEEDED, _T.NEEDS_REVISION, _T.FAILED},
    _T.NEEDS_REVISION: {_T.READY, _T.FAILED, _T.CANCELLED},
    _T.FAILED: {_T.READY, _T.CANCELLED},
    _T.SUCCEEDED: set(),
    _T.CANCELLED: set(),
}


class IllegalTransitionError(Exception):
    def __init__(self, task: ProjectTask, target: PMTaskStatus):
        super().__init__(
            f"task #{task.ordinal} '{task.title}': illegal transition "
            f"{task.status.value} → {target.value}"
        )
        self.current = task.status
        self.target = target


def transition(task: ProjectTask, target: PMTaskStatus) -> ProjectTask:
    """Apply a validated state transition (mutates the ORM object; caller commits)."""
    if target not in ALLOWED_TRANSITIONS[task.status]:
        raise IllegalTransitionError(task, target)
    task.status = target
    now = dt.datetime.now(dt.UTC)
    if target == _T.RUNNING and task.started_at is None:
        task.started_at = now
    if target in (_T.SUCCEEDED, _T.FAILED, _T.CANCELLED):
        task.finished_at = now
    if target == _T.READY:
        # A task back in the pool has no claim and no stale block reason.
        task.claimed_by = None
        task.claimed_at = None
        task.blocked_reason = None
    return task
