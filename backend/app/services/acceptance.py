"""Acceptance evaluation: did the attempt's deliverable actually satisfy the task?

Deterministic checks over persisted state — a task that "technically executed"
is distinct from one whose deliverable passed review. Results persist as
task_reviews rows; failures trigger bounded rework with explicit feedback.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, ProjectTask, RunStatus, RunTask, TaskReview, TaskStatus

# Artifact kind each agent's successful output represents.
AGENT_ARTIFACT_KINDS = {
    "research": "findings",
    "analyst": "analysis",
    "executor": "action_result",
    "reviewer": "review",
}


def build_result(task: ProjectTask, run: AgentRun, output: dict | None) -> dict:
    """Map a completed run's engine-task output into the project task result."""
    output = output or {}
    kind = AGENT_ARTIFACT_KINDS.get(task.agent_key or "", "output")
    artifact = {
        "kind": kind,
        "label": task.title,
        "data": {
            key: output.get(key)
            for key in ("summary", "findings", "metrics", "actions", "passed", "issues")
            if key in output
        },
        "evidence": output.get("evidence", []),
    }
    return {
        "run_id": str(run.id),
        "agent": task.agent_key,
        "output": output,
        "artifacts": [artifact],
    }


def _evaluate_criterion(criterion: dict, result: dict) -> tuple[bool, str]:
    kind = criterion.get("kind", "artifact_exists")
    output = result.get("output", {})
    if kind == "run_succeeded":
        return True, "run completed"  # only evaluated for completed runs
    if kind == "artifact_exists":
        wanted = criterion.get("config", {}).get("kind")
        artifacts = result.get("artifacts", [])
        present = [
            a
            for a in artifacts
            if a.get("data") and (wanted is None or a.get("kind") == wanted)
        ]
        if present:
            return True, f"{len(present)} artifact(s) present"
        return False, "no non-empty artifact" + (f" of kind '{wanted}'" if wanted else "")
    if kind == "output_field":
        fields = criterion.get("config", {}).get("field", "")
        value = output.get(fields)
        if value not in (None, "", [], {}):
            return True, f"field '{fields}' present"
        return False, f"required output field '{fields}' is missing or empty"
    if kind == "review_pass":
        if output.get("passed") is True:
            return True, "review passed"
        return False, "review did not pass" + (
            f": {'; '.join(str(i) for i in output.get('issues', [])[:3])}"
            if output.get("issues")
            else ""
        )
    return False, f"unknown criterion kind '{kind}'"


async def evaluate_acceptance(
    session: AsyncSession, task: ProjectTask, run: AgentRun, result: dict
) -> TaskReview:
    """Evaluate all acceptance criteria deterministically; persist the review."""
    criteria: list[dict] = [
        {"description": "the execution run completed successfully", "kind": "run_succeeded"}
    ]
    if task.acceptance_criteria:
        criteria.extend(task.acceptance_criteria)
    else:
        criteria.append(
            {
                "description": "the task produced a non-empty deliverable artifact",
                "kind": "artifact_exists",
            }
        )
    if task.agent_key == "reviewer":
        criteria.append(
            {"description": "the review verdict is PASS", "kind": "review_pass"}
        )

    results = []
    issues = []
    run_ok = run.status == RunStatus.COMPLETED
    for criterion in criteria:
        if criterion.get("kind") == "run_succeeded":
            passed, detail = run_ok, ("run completed" if run_ok else f"run {run.status.value}")
        else:
            passed, detail = _evaluate_criterion(criterion, result)
        results.append(
            {
                "description": criterion.get("description", criterion.get("kind")),
                "kind": criterion.get("kind"),
                "passed": passed,
                "detail": detail,
            }
        )
        if not passed:
            issues.append(f"{criterion.get('description')}: {detail}")

    review = TaskReview(
        organization_id=task.organization_id,
        task_id=task.id,
        attempt=task.attempts,
        passed=not issues,
        criteria_results=results,
        issues=issues,
        evidence=[
            {"kind": "run", "ref_id": str(run.id)},
            *[
                e
                for a in result.get("artifacts", [])
                for e in (a.get("evidence") or [])
                if isinstance(e, dict)
            ],
        ],
        reviewer="pm-deterministic",
    )
    session.add(review)
    await session.flush()
    return review


async def first_completed_output(session: AsyncSession, run_id: uuid.UUID) -> dict | None:
    """The MAIN engine-task output of a project-task run.

    Project-task runs may carry pre-completed synthetic tasks holding dependency
    outputs at lower ordinals; the real work is always the highest ordinal.
    """
    from sqlalchemy import select

    row = await session.scalar(
        select(RunTask)
        .where(RunTask.run_id == run_id, RunTask.status == TaskStatus.COMPLETED)
        .order_by(RunTask.ordinal.desc())
    )
    return row.output if row else None
