"""Project + task lifecycle service (HTTP-agnostic).

All state-changing operations validate deterministically here: the PM proposes,
this layer enforces. Tenant scoping is explicit on every query.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.capabilities import check_assignment, get_capability
from app.models import (
    ActorType,
    Document,
    PMTaskStatus,
    Project,
    ProjectStatus,
    ProjectTask,
    TaskDependency,
    TaskReview,
)
from app.services.audit import record_audit
from app.services.project_events import ProjectEventRecorder
from app.services.task_graph import GraphValidationError, validate_dag
from app.services.task_state import transition

PM_ACTOR = "pm"


class ProjectServiceError(Exception):
    """Safe, user-facing message."""


async def get_project(
    session: AsyncSession, organization_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id, Project.organization_id == organization_id
        )
    )
    if project is None:
        raise ProjectServiceError("project not found")
    return project


async def create_project(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    goal: str,
    settings: dict | None = None,
) -> Project:
    project = Project(
        organization_id=organization_id,
        name=name,
        goal=goal,
        description=goal[:500],
        status=ProjectStatus.DRAFT.value,
        settings=settings or {},
        created_by=user_id,
    )
    session.add(project)
    await session.flush()
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=organization_id,
        type="project.created",
        payload={"name": name, "goal": goal[:300]},
    )
    await record_audit(
        session,
        action="project.created",
        actor_type=ActorType.USER,
        actor_id=str(user_id),
        organization_id=organization_id,
        entity_type="project",
        entity_id=str(project.id),
    )
    return project


async def project_tasks(session: AsyncSession, project_id: uuid.UUID) -> list[ProjectTask]:
    return list(
        (
            await session.scalars(
                select(ProjectTask)
                .where(ProjectTask.project_id == project_id)
                .order_by(ProjectTask.ordinal)
            )
        ).all()
    )


async def project_edges(
    session: AsyncSession, project_id: uuid.UUID
) -> list[TaskDependency]:
    return list(
        (
            await session.scalars(
                select(TaskDependency)
                .join(ProjectTask, ProjectTask.id == TaskDependency.task_id)
                .where(ProjectTask.project_id == project_id)
            )
        ).all()
    )


async def _next_ordinal(session: AsyncSession, project_id: uuid.UUID) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(ProjectTask.ordinal), 0)).where(
            ProjectTask.project_id == project_id
        )
    )
    return (current or 0) + 1


async def resolve_task_ref(
    session: AsyncSession, project: Project, ref: str
) -> ProjectTask:
    """Resolve '#3', '3', a task UUID, or a title fragment to one task."""
    ref = ref.strip()
    candidate = ref.lstrip("#")
    if candidate.isdigit():
        task = await session.scalar(
            select(ProjectTask).where(
                ProjectTask.project_id == project.id,
                ProjectTask.ordinal == int(candidate),
            )
        )
        if task:
            return task
    try:
        task = await session.scalar(
            select(ProjectTask).where(
                ProjectTask.project_id == project.id,
                ProjectTask.id == uuid.UUID(ref),
            )
        )
        if task:
            return task
    except ValueError:
        pass
    matches = list(
        (
            await session.scalars(
                select(ProjectTask).where(
                    ProjectTask.project_id == project.id,
                    ProjectTask.title.ilike(f"%{ref}%"),
                )
            )
        ).all()
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ProjectServiceError(f"no task matches '{ref}'")
    raise ProjectServiceError(
        f"'{ref}' matches several tasks: "
        + ", ".join(f"#{t.ordinal} {t.title}" for t in matches[:5])
    )


async def validate_project_dag(
    session: AsyncSession, project_id: uuid.UUID, extra_edges: list[tuple[str, str]] | None = None
) -> None:
    """Server-side DAG validation of the persisted graph (+ optional new edges)."""
    tasks = await project_tasks(session, project_id)
    edges = [
        (str(dep.task_id), str(dep.depends_on_task_id))
        for dep in await project_edges(session, project_id)
    ]
    if extra_edges:
        edges.extend(extra_edges)
    try:
        validate_dag([str(t.id) for t in tasks], edges)
    except GraphValidationError as exc:
        raise ProjectServiceError(str(exc)) from exc


async def supplied_artifact_kinds(
    session: AsyncSession, organization_id: uuid.UUID, task: ProjectTask
) -> list[str]:
    """Artifact kinds available to a task: supplied by the owner (verified) plus
    those produced by *succeeded* dependencies. Never inferred from text."""
    kinds: list[str] = []
    for artifact in task.params.get("supplied_artifacts", []):
        kind = str(artifact.get("kind", ""))
        ref_id = artifact.get("ref_id")
        if kind == "document" and ref_id:
            exists = await session.scalar(
                select(Document.id).where(
                    Document.id == uuid.UUID(str(ref_id)),
                    Document.organization_id == organization_id,
                )
            )
            if exists:
                kinds.append("document")
        elif kind:
            # Non-document supplied artifacts must carry inline data to count.
            if artifact.get("data"):
                kinds.append(kind)
    dep_rows = (
        await session.scalars(
            select(ProjectTask)
            .join(TaskDependency, TaskDependency.depends_on_task_id == ProjectTask.id)
            .where(TaskDependency.task_id == task.id)
        )
    ).all()
    for dep in dep_rows:
        if dep.status == PMTaskStatus.SUCCEEDED and dep.result:
            for artifact in dep.result.get("artifacts", []):
                if artifact.get("kind"):
                    kinds.append(str(artifact["kind"]))
    return kinds


async def create_task(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    user_id: uuid.UUID | None,
    title: str,
    description: str = "",
    agent_key: str | None = None,
    requested_agent_key: str | None = None,
    depends_on_ordinals: list[int] | None = None,
    params: dict | None = None,
    acceptance_criteria: list[dict] | None = None,
    expected_deliverables: list[str] | None = None,
    supplied_artifacts: list[dict] | None = None,
    priority: int = 100,
    status: PMTaskStatus = PMTaskStatus.PLANNED,
    revision_of: uuid.UUID | None = None,
    revision_feedback: str | None = None,
    actor: str = "owner",
) -> ProjectTask:
    if agent_key is not None and get_capability(agent_key) is None:
        raise ProjectServiceError(f"no registered agent with key '{agent_key}'")

    merged_params = dict(params or {})
    if supplied_artifacts:
        merged_params["supplied_artifacts"] = supplied_artifacts

    task = ProjectTask(
        organization_id=project.organization_id,
        project_id=project.id,
        ordinal=await _next_ordinal(session, project.id),
        title=title,
        description=description,
        status=status,
        priority=priority,
        agent_key=agent_key,
        requested_agent_key=requested_agent_key,
        params=merged_params,
        acceptance_criteria=acceptance_criteria or [],
        expected_deliverables=expected_deliverables or [],
        revision_of=revision_of,
        revision_feedback=revision_feedback,
    )
    session.add(task)
    await session.flush()

    for ordinal in depends_on_ordinals or []:
        dep_task = await session.scalar(
            select(ProjectTask).where(
                ProjectTask.project_id == project.id, ProjectTask.ordinal == ordinal
            )
        )
        if dep_task is None:
            raise ProjectServiceError(f"dependency #{ordinal} does not exist")
        session.add(TaskDependency(task_id=task.id, depends_on_task_id=dep_task.id))
    await session.flush()
    await validate_project_dag(session, project.id)

    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.created",
        payload={
            "ordinal": task.ordinal,
            "title": title,
            "agent": agent_key,
            "depends_on": depends_on_ordinals or [],
            "by": actor,
            "revision_of": str(revision_of) if revision_of else None,
        },
    )
    await record_audit(
        session,
        action="project_task.created",
        actor_type=ActorType.USER if actor == "owner" else ActorType.AGENT,
        actor_id=str(user_id) if user_id else PM_ACTOR,
        organization_id=project.organization_id,
        entity_type="project_task",
        entity_id=str(task.id),
        meta={"title": title, "agent": agent_key},
    )
    return task


async def assign_agent(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    task: ProjectTask,
    agent_key: str,
    requested: bool,
    actor_id: str,
) -> ProjectTask:
    """Record an assignment after capability validation (artifacts are checked at
    scheduling time too — this validates the agent itself)."""
    capability = get_capability(agent_key)
    if capability is None:
        raise ProjectServiceError(f"no registered agent with key '{agent_key}'")
    if not capability.assignable:
        raise ProjectServiceError(capability.unassignable_reason)
    if task.status in (PMTaskStatus.SUCCEEDED, PMTaskStatus.CANCELLED):
        raise ProjectServiceError(
            f"task #{task.ordinal} is {task.status.value} and cannot be reassigned"
        )
    if task.status in (PMTaskStatus.ASSIGNED, PMTaskStatus.RUNNING, PMTaskStatus.REVIEWING):
        raise ProjectServiceError(
            f"task #{task.ordinal} is currently {task.status.value}; cancel or wait first"
        )
    task.agent_key = agent_key
    if requested:
        task.requested_agent_key = agent_key
    task.max_attempts = capability.max_attempts
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.assigned",
        payload={"ordinal": task.ordinal, "agent": agent_key, "requested": requested},
    )
    await record_audit(
        session,
        action="project_task.assigned",
        actor_type=ActorType.USER,
        actor_id=actor_id,
        organization_id=project.organization_id,
        entity_type="project_task",
        entity_id=str(task.id),
        meta={"agent": agent_key},
    )
    return task


async def readiness_inputs(
    session: AsyncSession, task: ProjectTask
) -> list[tuple[str, PMTaskStatus, str]]:
    rows = (
        await session.execute(
            select(ProjectTask.id, ProjectTask.status, ProjectTask.title)
            .join(TaskDependency, TaskDependency.depends_on_task_id == ProjectTask.id)
            .where(TaskDependency.task_id == task.id)
        )
    ).all()
    return [(str(r[0]), r[1], r[2]) for r in rows]


async def eligibility(
    session: AsyncSession, project: Project, task: ProjectTask
) -> tuple[bool, str | None]:
    """Full eligibility: dependencies + capability input artifacts + agent set.
    Returns (eligible, blocking_reason)."""
    from app.services.task_graph import compute_readiness

    if task.agent_key is None:
        return False, "no agent assigned"
    readiness = compute_readiness(await readiness_inputs(session, task))
    if not readiness.ready:
        return False, readiness.blocked_reason
    kinds = await supplied_artifact_kinds(session, project.organization_id, task)
    check = check_assignment(task.agent_key, kinds)
    if not check.allowed:
        return False, check.reason
    return True, None


async def cancel_task(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    task: ProjectTask,
    actor_id: str,
) -> ProjectTask:
    if task.status in (PMTaskStatus.SUCCEEDED, PMTaskStatus.CANCELLED):
        raise ProjectServiceError(f"task #{task.ordinal} is already {task.status.value}")
    if task.status == PMTaskStatus.RUNNING and task.run_id is not None:
        # Cooperative: flag the underlying run; the engine finalizes the task.
        from app.models import AgentRun

        run = await session.get(AgentRun, task.run_id)
        if run is not None:
            run.cancel_requested = True
    transition(task, PMTaskStatus.CANCELLED)
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.cancelled",
        payload={"ordinal": task.ordinal, "by": actor_id},
    )
    return task


async def retry_task(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    task: ProjectTask,
    actor_id: str,
) -> ProjectTask:
    if task.status != PMTaskStatus.FAILED:
        raise ProjectServiceError(
            f"only failed tasks can be retried (task #{task.ordinal} is {task.status.value})"
        )
    task.error = None
    task.attempts = 0
    transition(task, PMTaskStatus.READY)
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type="task.retried",
        payload={"ordinal": task.ordinal, "by": actor_id},
    )
    return task


async def set_project_status(
    session: AsyncSession,
    recorder: ProjectEventRecorder,
    *,
    project: Project,
    status: ProjectStatus,
    actor_id: str,
) -> Project:
    allowed = {
        ProjectStatus.ACTIVE.value: {ProjectStatus.PAUSED, ProjectStatus.CANCELLED},
        ProjectStatus.PAUSED.value: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
        ProjectStatus.DRAFT.value: {ProjectStatus.CANCELLED, ProjectStatus.ACTIVE},
        ProjectStatus.AWAITING_APPROVAL.value: {ProjectStatus.CANCELLED},
        ProjectStatus.PLANNING.value: {ProjectStatus.CANCELLED},
    }
    if status not in allowed.get(project.status, set()):
        raise ProjectServiceError(
            f"cannot move project from {project.status} to {status.value}"
        )
    project.status = status.value
    event = {
        ProjectStatus.PAUSED: "project.paused",
        ProjectStatus.ACTIVE: "project.resumed",
        ProjectStatus.CANCELLED: "project.cancelled",
    }[status]
    await recorder.emit(
        session,
        project_id=project.id,
        organization_id=project.organization_id,
        type=event,
        payload={"by": actor_id},
    )
    await record_audit(
        session,
        action=event,
        actor_type=ActorType.USER,
        actor_id=actor_id,
        organization_id=project.organization_id,
        entity_type="project",
        entity_id=str(project.id),
    )
    if status == ProjectStatus.CANCELLED:
        for task in await project_tasks(session, project.id):
            if task.status not in (
                PMTaskStatus.SUCCEEDED,
                PMTaskStatus.CANCELLED,
                PMTaskStatus.FAILED,
            ):
                transition(task, PMTaskStatus.CANCELLED)
    return project


async def task_reviews(session: AsyncSession, task_id: uuid.UUID) -> list[TaskReview]:
    return list(
        (
            await session.scalars(
                select(TaskReview)
                .where(TaskReview.task_id == task_id)
                .order_by(TaskReview.attempt)
            )
        ).all()
    )
