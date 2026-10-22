"""Run endpoints: lifecycle, details, events (replay + SSE live stream)."""

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings
from app.core.deps import OrgCtx, SessionDep, rate_limit, require_role
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import TokenError, create_stream_token, decode_token
from app.db.session import get_sessionmaker
from app.events.recorder import EventRecorder, channel_for_run
from app.models import (
    AgentRun,
    ApprovalRequest,
    ExecutionEvent,
    MemberRole,
    OrganizationMember,
    Report,
    RunStatus,
    RunTask,
    ToolCall,
)
from app.schemas.runs import (
    ApprovalOut,
    CreateRunRequest,
    EventOut,
    ReportOut,
    RunDetailOut,
    RunListOut,
    RunSummaryOut,
    StreamTokenResponse,
    ToolCallOut,
)
from app.services.runs import RunServiceError, create_run, enqueue_run, request_cancel, retry_run

logger = get_logger("agentos.api.runs")
router = APIRouter(prefix="/runs", tags=["runs"])

_member = require_role(MemberRole.MEMBER)


async def _get_org_run(session, ctx, run_id: uuid.UUID, *, with_children: bool = False) -> AgentRun:
    query = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.organization_id == ctx.organization.id,  # tenant scope
    )
    if with_children:
        query = query.options(selectinload(AgentRun.tasks))
    run = await session.scalar(query)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.post("", response_model=RunSummaryOut, dependencies=[Depends(rate_limit("runs", 30))])
async def submit_run(
    body: CreateRunRequest, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    recorder = EventRecorder()
    run = await create_run(
        session,
        recorder,
        organization_id=ctx.organization.id,
        user_id=ctx.user.id,
        input_text=body.input_text,
        title=body.title,
        params=body.params,
        project_id=body.project_id,
    )
    await session.commit()
    await recorder.publish_pending()
    await enqueue_run(run.id)
    return RunSummaryOut.model_validate(run)


@router.get("", response_model=RunListOut)
async def list_runs(
    session: SessionDep,
    ctx: OrgCtx,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    query = select(AgentRun).where(AgentRun.organization_id == ctx.organization.id)
    if status_filter:
        try:
            query = query.where(AgentRun.status == RunStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown status") from exc
    if q:
        query = query.where(AgentRun.title.ilike(f"%{q}%") | AgentRun.input_text.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = (
        await session.scalars(
            query.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    agents_by_run: dict = {}
    if rows:
        task_rows = (
            await session.execute(
                select(RunTask.run_id, RunTask.agent_key)
                .where(RunTask.run_id.in_([r.id for r in rows]))
                .distinct()
            )
        ).all()
        for run_id, agent_key in task_rows:
            agents_by_run.setdefault(run_id, []).append(agent_key)
    items = []
    for run in rows:
        item = RunSummaryOut.model_validate(run)
        item.agents = sorted(agents_by_run.get(run.id, []))
        items.append(item)
    return RunListOut(items=items, total=total)


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    run = await _get_org_run(session, ctx, run_id, with_children=True)
    tool_calls = (
        await session.scalars(
            select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.created_at)
        )
    ).all()
    approvals = (
        await session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.run_id == run.id)
            .order_by(ApprovalRequest.created_at)
        )
    ).all()
    report = await session.scalar(
        select(Report).where(Report.run_id == run.id).order_by(Report.created_at.desc())
    )
    detail = RunDetailOut.model_validate(run, from_attributes=True)
    detail.tool_calls = [ToolCallOut.model_validate(t) for t in tool_calls]
    detail.approvals = [ApprovalOut.model_validate(a) for a in approvals]
    detail.report = ReportOut.model_validate(report) if report else None
    return detail


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
async def download_report(run_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    run = await _get_org_run(session, ctx, run_id)
    report = await session.scalar(
        select(Report).where(Report.run_id == run.id).order_by(Report.created_at.desc())
    )
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report for this run")
    return PlainTextResponse(
        report.body_markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'},
    )


@router.post("/{run_id}/cancel", response_model=RunSummaryOut)
async def cancel_run(
    run_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    run = await _get_org_run(session, ctx, run_id)
    recorder = EventRecorder()
    try:
        run = await request_cancel(session, recorder, run=run, user_id=ctx.user.id)
    except RunServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    return RunSummaryOut.model_validate(run)


@router.post("/{run_id}/retry", response_model=RunSummaryOut)
async def retry_failed_run(
    run_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    run = await _get_org_run(session, ctx, run_id)
    recorder = EventRecorder()
    try:
        run = await retry_run(session, recorder, run=run, user_id=ctx.user.id)
    except RunServiceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    await recorder.publish_pending()
    await enqueue_run(run.id)
    return RunSummaryOut.model_validate(run)


@router.get("/{run_id}/events", response_model=list[EventOut])
async def list_events(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: OrgCtx,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    await _get_org_run(session, ctx, run_id)
    events = (
        await session.scalars(
            select(ExecutionEvent)
            .where(ExecutionEvent.run_id == run_id, ExecutionEvent.seq > after)
            .order_by(ExecutionEvent.seq)
            .limit(limit)
        )
    ).all()
    return [
        EventOut(seq=e.seq, type=e.type, payload=e.payload, created_at=e.created_at)
        for e in events
    ]


@router.post("/{run_id}/stream-token", response_model=StreamTokenResponse)
async def mint_stream_token(run_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    """EventSource cannot send headers; mint a short-lived single-purpose token."""
    await _get_org_run(session, ctx, run_id)
    settings = get_settings()
    return StreamTokenResponse(
        token=create_stream_token(ctx.user.id, run_id),
        expires_in_s=settings.stream_token_seconds,
    )


@router.get("/{run_id}/events/stream")
async def stream_events(run_id: uuid.UUID, request: Request, token: str):
    """SSE stream: replays persisted events after Last-Event-ID, then relays live
    pub/sub messages. Every message id is the event's per-run seq."""
    try:
        payload = decode_token(token, expected_purpose="stream")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    if payload.get("run_id") != str(run_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token does not match this run")
    user_id = uuid.UUID(payload["sub"])

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        membership = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == run.organization_id,
            )
        )
        if membership is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this run")

    last_id_header = (
        request.headers.get("last-event-id") or request.query_params.get("after") or "0"
    )
    try:
        after_seq = int(last_id_header)
    except ValueError:
        after_seq = 0

    terminal_types = {"run.completed", "run.failed", "run.cancelled"}

    async def event_generator():
        # Subscribe first so no event is lost between replay and live phases;
        # duplicates are filtered by seq.
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel_for_run(run_id))
        delivered = after_seq
        try:
            async with sessionmaker() as session:
                rows = (
                    await session.scalars(
                        select(ExecutionEvent)
                        .where(ExecutionEvent.run_id == run_id, ExecutionEvent.seq > after_seq)
                        .order_by(ExecutionEvent.seq)
                    )
                ).all()
            finished = False
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
                if event.type in terminal_types:
                    finished = True
            while not finished:
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
                if data.get("type") in terminal_types:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel_for_run(run_id))
            await pubsub.aclose()

    return EventSourceResponse(event_generator())
