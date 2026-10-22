"""PM harness endpoints: projects, plans, tasks, PM conversation, events."""

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.agents.pm import PlanDraftError
from app.core.deps import OrgCtx, SessionDep, rate_limit, require_role
from app.core.security import TokenError, create_project_stream_token, decode_token
from app.db.session import get_sessionmaker
from app.models import (
    Conversation,
    ConversationMessage,
    MemberRole,
    OrganizationMember,
    Project,
    ProjectEvent,
    ProjectPlan,
    ProjectStatus,
    ProjectTask,
)
from app.schemas.pm import (
    AssignTaskRequest,
    CreateTaskRequest,
    GraphNodeOut,
    GraphOut,
    PlanOut,
    PMMessageOut,
    PMMessageRequest,
    ProjectCreateRequest,
    ProjectDetailOut,
    ProjectEventOut,
    ProjectOut,
    TaskDetailOut,
    TaskOut,
)
from app.services import pm as pm_service
from app.services import projects as project_service
from app.services.project_events import ProjectEventRecorder, channel_for_project
from app.services.projects import ProjectServiceError

router = APIRouter(prefix="/projects", tags=["projects"])

_member = require_role(MemberRole.MEMBER)
_admin = require_role(MemberRole.ADMIN)


async def _project_or_404(session, ctx, project_id: uuid.UUID) -> Project:
    try:
        return await project_service.get_project(session, ctx.organization.id, project_id)
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


async def _task_or_404(session, project: Project, task_id: uuid.UUID) -> ProjectTask:
    task = await session.scalar(
        select(ProjectTask).where(
            ProjectTask.id == task_id, ProjectTask.project_id == project.id
        )
    )
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return task


async def _edges_by_task(session, project_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    edges: dict[uuid.UUID, list[uuid.UUID]] = {}
    for dep in await project_service.project_edges(session, project_id):
        edges.setdefault(dep.task_id, []).append(dep.depends_on_task_id)
    return edges


async def _serialize_tasks(session, project_id: uuid.UUID) -> list[TaskOut]:
    tasks = await project_service.project_tasks(session, project_id)
    ordinal_by_id = {t.id: t.ordinal for t in tasks}
    edges = await _edges_by_task(session, project_id)
    out = []
    for task in tasks:
        item = TaskOut.model_validate(task)
        item.depends_on = sorted(
            ordinal_by_id[d] for d in edges.get(task.id, []) if d in ordinal_by_id
        )
        out.append(item)
    return out


# ------------------------------------------------------------- projects


@router.post("", response_model=ProjectOut, dependencies=[Depends(rate_limit("projects", 30))])
async def create_project(
    body: ProjectCreateRequest, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    recorder = ProjectEventRecorder()
    project = await project_service.create_project(
        session,
        recorder,
        organization_id=ctx.organization.id,
        user_id=ctx.user.id,
        name=body.name,
        goal=body.goal,
        settings=body.settings,
    )
    await session.commit()
    await recorder.publish_pending()
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: SessionDep, ctx: OrgCtx):
    rows = (
        await session.scalars(
            select(Project)
            .where(Project.organization_id == ctx.organization.id)
            .order_by(Project.created_at.desc())
        )
    ).all()
    return [ProjectOut.model_validate(p) for p in rows]


@router.get("/{project_id}", response_model=ProjectDetailOut)
async def get_project(project_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    project = await _project_or_404(session, ctx, project_id)
    tasks = await _serialize_tasks(session, project.id)
    plans = (
        await session.scalars(
            select(ProjectPlan)
            .where(ProjectPlan.project_id == project.id)
            .order_by(ProjectPlan.version.desc())
        )
    ).all()
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    detail = ProjectDetailOut.model_validate(project, from_attributes=True)
    detail.tasks = tasks
    detail.plans = [PlanOut.model_validate(p) for p in plans]
    detail.task_counts = counts
    return detail


class ProjectActionResponse(BaseModel):
    status: str
    message: str = ""


@router.post("/{project_id}/pause", response_model=ProjectOut)
@router.post("/{project_id}/resume", response_model=ProjectOut)
@router.post("/{project_id}/cancel", response_model=ProjectOut)
async def project_lifecycle(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    action = request.url.path.rsplit("/", 1)[-1]
    target = {
        "pause": ProjectStatus.PAUSED,
        "resume": ProjectStatus.ACTIVE,
        "cancel": ProjectStatus.CANCELLED,
    }[action]
    recorder = ProjectEventRecorder()
    try:
        await project_service.set_project_status(
            session, recorder, project=project, status=target, actor_id=str(ctx.user.id)
        )
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    if target == ProjectStatus.ACTIVE:
        from app.services.queueing import try_enqueue
        from app.worker.tasks import schedule_project_task

        # Non-fatal if the queue is down: tasks stay READY and a later
        # /schedule call or PM "run ready" dispatches them.
        await try_enqueue(schedule_project_task, str(project.id))
    return ProjectOut.model_validate(project)


@router.post("/{project_id}/schedule", response_model=ProjectActionResponse)
async def schedule_now(
    project_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    project = await _project_or_404(session, ctx, project_id)
    if project.status != ProjectStatus.ACTIVE.value:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"project is {project.status}, not active"
        )
    from app.services.project_scheduler import schedule_project

    dispatched = await schedule_project(str(project.id))
    return ProjectActionResponse(
        status="ok", message=f"dispatched {len(dispatched)} task(s)"
    )


# ----------------------------------------------------------------- plans


@router.post("/{project_id}/plan", response_model=PlanOut)
async def propose_plan(
    project_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    project = await _project_or_404(session, ctx, project_id)
    recorder = ProjectEventRecorder()
    try:
        plan = await pm_service.propose_plan(
            session, recorder, project=project, user_id=ctx.user.id
        )
    except (PlanDraftError, ProjectServiceError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    return PlanOut.model_validate(plan)


class PlanDecisionRequest(BaseModel):
    approve: bool


@router.post("/{project_id}/plans/{version}/decide", response_model=PlanOut)
async def decide_plan(
    project_id: uuid.UUID,
    version: int,
    body: PlanDecisionRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_admin)],
):
    project = await _project_or_404(session, ctx, project_id)
    plan = await session.scalar(
        select(ProjectPlan).where(
            ProjectPlan.project_id == project.id, ProjectPlan.version == version
        )
    )
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan version not found")
    recorder = ProjectEventRecorder()
    try:
        plan = await pm_service.decide_plan(
            session,
            recorder,
            project=project,
            plan=plan,
            approve=body.approve,
            user_id=ctx.user.id,
        )
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    if body.approve:
        from app.services.queueing import try_enqueue
        from app.worker.tasks import schedule_project_task

        # Non-fatal if the queue is down: tasks stay READY and a later
        # /schedule call or PM "run ready" dispatches them.
        await try_enqueue(schedule_project_task, str(project.id))
    return PlanOut.model_validate(plan)


# ----------------------------------------------------------------- tasks


@router.post("/{project_id}/tasks", response_model=TaskOut)
async def create_task(
    project_id: uuid.UUID,
    body: CreateTaskRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    recorder = ProjectEventRecorder()
    try:
        task = await project_service.create_task(
            session,
            recorder,
            project=project,
            user_id=ctx.user.id,
            title=body.title,
            description=body.description,
            agent_key=body.agent_key,
            requested_agent_key=body.agent_key,
            depends_on_ordinals=body.depends_on,
            params=body.params,
            acceptance_criteria=[c.model_dump() for c in body.acceptance_criteria],
            supplied_artifacts=body.supplied_artifacts,
            priority=body.priority,
        )
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if project.status == ProjectStatus.DRAFT.value:
        project.status = ProjectStatus.ACTIVE.value
    await session.commit()
    await recorder.publish_pending()
    item = TaskOut.model_validate(task)
    item.depends_on = body.depends_on
    return item


@router.get("/{project_id}/tasks/{task_id}", response_model=TaskDetailOut)
async def get_task(
    project_id: uuid.UUID, task_id: uuid.UUID, session: SessionDep, ctx: OrgCtx
):
    project = await _project_or_404(session, ctx, project_id)
    task = await _task_or_404(session, project, task_id)
    tasks = await project_service.project_tasks(session, project.id)
    ordinal_by_id = {t.id: t.ordinal for t in tasks}
    edges = await _edges_by_task(session, project.id)
    detail = TaskDetailOut.model_validate(task, from_attributes=True)
    detail.depends_on = sorted(
        ordinal_by_id[d] for d in edges.get(task.id, []) if d in ordinal_by_id
    )
    detail.dependents = sorted(
        ordinal_by_id[t_id]
        for t_id, deps in edges.items()
        if task.id in deps and t_id in ordinal_by_id
    )
    detail.reviews = [
        {
            "attempt": r.attempt,
            "passed": r.passed,
            "criteria_results": r.criteria_results,
            "issues": r.issues,
            "reviewer": r.reviewer,
            "created_at": r.created_at.isoformat(),
        }
        for r in await project_service.task_reviews(session, task.id)
    ]
    return detail


class TaskActionResponse(BaseModel):
    status: str
    message: str
    plan_version: int | None = None


@router.post("/{project_id}/tasks/{task_id}/assign", response_model=TaskActionResponse)
async def assign_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: AssignTaskRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    task = await _task_or_404(session, project, task_id)
    recorder = ProjectEventRecorder()
    try:
        result = await pm_service.request_agent(
            session,
            recorder,
            project=project,
            task=task,
            agent_key=body.agent_key,
            mode=body.mode,
            user_id=ctx.user.id,
        )
    except (ProjectServiceError, PlanDraftError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    return TaskActionResponse(
        status=result["status"],
        message=result["message"],
        plan_version=result.get("plan_version"),
    )


@router.post("/{project_id}/tasks/{task_id}/retry", response_model=TaskActionResponse)
async def retry_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    task = await _task_or_404(session, project, task_id)
    recorder = ProjectEventRecorder()
    try:
        await project_service.retry_task(
            session, recorder, project=project, task=task, actor_id=str(ctx.user.id)
        )
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    from app.services.queueing import try_enqueue
    from app.worker.tasks import schedule_project_task

    scheduled = await try_enqueue(schedule_project_task, str(project.id))
    return TaskActionResponse(
        status="ok",
        message=f"task #{task.ordinal} re-queued"
        + ("" if scheduled else " (queue unavailable — dispatch it later via /schedule)"),
    )


@router.post("/{project_id}/tasks/{task_id}/cancel", response_model=TaskActionResponse)
async def cancel_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    task = await _task_or_404(session, project, task_id)
    recorder = ProjectEventRecorder()
    try:
        await project_service.cancel_task(
            session, recorder, project=project, task=task, actor_id=str(ctx.user.id)
        )
    except ProjectServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    return TaskActionResponse(status="ok", message=f"task #{task.ordinal} cancelled")


# ------------------------------------------------------------------ graph


@router.get("/{project_id}/graph", response_model=GraphOut)
async def project_graph(project_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    project = await _project_or_404(session, ctx, project_id)
    tasks = await project_service.project_tasks(session, project.id)
    edges = await project_service.project_edges(session, project.id)
    return GraphOut(
        nodes=[
            GraphNodeOut(
                id=str(t.id),
                ordinal=t.ordinal,
                title=t.title,
                agent_key=t.agent_key,
                status=t.status,
                blocked_reason=t.blocked_reason,
            )
            for t in tasks
        ],
        edges=[
            {"source": str(d.depends_on_task_id), "target": str(d.task_id)} for d in edges
        ],
    )


# ---------------------------------------------------------- conversation


@router.post(
    "/{project_id}/pm/messages",
    response_model=PMMessageOut,
    dependencies=[Depends(rate_limit("pm", 30))],
)
async def send_pm_message(
    project_id: uuid.UUID,
    body: PMMessageRequest,
    session: SessionDep,
    ctx: Annotated[OrgCtx, Depends(_member)],
):
    project = await _project_or_404(session, ctx, project_id)
    recorder = ProjectEventRecorder()
    reply = await pm_service.handle_pm_message(
        session, recorder, project=project, user_id=ctx.user.id, content=body.content
    )
    await session.commit()
    await recorder.publish_pending()
    return PMMessageOut(
        id=reply.id,
        role=reply.role,
        content=reply.content,
        meta=reply.meta,
        created_at=reply.created_at,
    )


@router.get("/{project_id}/pm/messages", response_model=list[PMMessageOut])
async def list_pm_messages(project_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    project = await _project_or_404(session, ctx, project_id)
    conversation = await session.scalar(
        select(Conversation).where(Conversation.project_id == project.id)
    )
    if conversation is None:
        return []
    rows = (
        await session.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at)
        )
    ).all()
    return [
        PMMessageOut(
            id=m.id, role=m.role, content=m.content, meta=m.meta, created_at=m.created_at
        )
        for m in rows
    ]


# ---------------------------------------------------------------- events


@router.get("/{project_id}/events", response_model=list[ProjectEventOut])
async def list_events(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: OrgCtx,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    project = await _project_or_404(session, ctx, project_id)
    events = (
        await session.scalars(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project.id, ProjectEvent.seq > after)
            .order_by(ProjectEvent.seq)
            .limit(limit)
        )
    ).all()
    return [
        ProjectEventOut(seq=e.seq, type=e.type, payload=e.payload, created_at=e.created_at)
        for e in events
    ]


class StreamTokenOut(BaseModel):
    token: str


@router.post("/{project_id}/stream-token", response_model=StreamTokenOut)
async def mint_stream_token(project_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    await _project_or_404(session, ctx, project_id)
    return StreamTokenOut(token=create_project_stream_token(ctx.user.id, project_id))


@router.get("/{project_id}/events/stream")
async def stream_events(project_id: uuid.UUID, request: Request, token: str):
    try:
        payload = decode_token(token, expected_purpose="project-stream")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if payload.get("project_id") != str(project_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token does not match this project")
    user_id = uuid.UUID(payload["sub"])

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
        membership = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == project.organization_id,
            )
        )
        if membership is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this project")

    after_raw = request.headers.get("last-event-id") or request.query_params.get("after") or "0"
    try:
        after_seq = int(after_raw)
    except ValueError:
        after_seq = 0

    async def generator():
        from app.core.redis import get_redis

        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_for_project(project_id))
        delivered = after_seq
        try:
            async with sessionmaker() as session:
                rows = (
                    await session.scalars(
                        select(ProjectEvent)
                        .where(
                            ProjectEvent.project_id == project_id,
                            ProjectEvent.seq > after_seq,
                        )
                        .order_by(ProjectEvent.seq)
                    )
                ).all()
            for event in rows:
                delivered = max(delivered, event.seq)
                yield {
                    "id": str(event.seq),
                    "event": event.type,
                    "data": json.dumps(
                        {
                            "seq": event.seq,
                            "type": event.type,
                            "payload": event.payload,
                            "created_at": event.created_at.isoformat(),
                        },
                        default=str,
                    ),
                }
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message is None:
                    yield {"comment": "heartbeat"}
                    continue
                try:
                    data = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                seq = int(data.get("id", 0))
                if seq <= delivered:
                    continue
                delivered = seq
                yield {
                    "id": str(seq),
                    "event": data.get("type", "event"),
                    "data": json.dumps(
                        {
                            "seq": seq,
                            "type": data.get("type"),
                            "payload": data.get("payload"),
                            "created_at": data.get("created_at"),
                        },
                        default=str,
                    ),
                }
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel_for_project(project_id))
            await pubsub.aclose()

    return EventSourceResponse(generator())
