"""Capability/status metadata and real run statistics. Never returns secret values."""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.deps import OrgCtx, SessionDep
from app.db.capabilities import get_capabilities
from app.models import AgentRun, Document, RunStatus, ToolCall

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/overview")
async def overview(session: SessionDep, ctx: OrgCtx) -> dict:
    """Real aggregates for the Overview page — no fabricated metrics."""
    org_id = ctx.organization.id
    status_rows = (
        await session.execute(
            select(AgentRun.status, func.count())
            .where(AgentRun.organization_id == org_id)
            .group_by(AgentRun.status)
        )
    ).all()
    by_status = {status.value: count for status, count in status_rows}
    avg_duration = await session.scalar(
        select(
            func.avg(
                func.extract("epoch", AgentRun.finished_at)
                - func.extract("epoch", AgentRun.started_at)
            )
        ).where(
            AgentRun.organization_id == org_id,
            AgentRun.status == RunStatus.COMPLETED,
            AgentRun.started_at.isnot(None),
            AgentRun.finished_at.isnot(None),
        )
    )
    tool_call_count = await session.scalar(
        select(func.count(ToolCall.id)).where(ToolCall.organization_id == org_id)
    )
    document_count = await session.scalar(
        select(func.count(Document.id)).where(Document.organization_id == org_id)
    )
    tokens = (
        await session.execute(
            select(
                func.sum(AgentRun.tokens_input), func.sum(AgentRun.tokens_output)
            ).where(AgentRun.organization_id == org_id)
        )
    ).one()
    recent = (
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.organization_id == org_id)
            .order_by(AgentRun.created_at.desc())
            .limit(8)
        )
    ).all()
    return {
        "runs_by_status": by_status,
        "runs_total": sum(by_status.values()),
        "avg_completed_duration_s": round(float(avg_duration), 2) if avg_duration else None,
        "tool_calls_total": tool_call_count or 0,
        "documents_total": document_count or 0,
        "tokens_input_total": int(tokens[0]) if tokens[0] is not None else None,
        "tokens_output_total": int(tokens[1]) if tokens[1] is not None else None,
        "recent_runs": [
            {
                "id": str(r.id),
                "title": r.title,
                "status": r.status.value,
                "mode": r.mode.value,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent
        ],
    }


@router.get("/capabilities")
async def capabilities(_ctx: OrgCtx) -> dict:
    settings = get_settings()
    caps = get_capabilities()
    return {
        "llm": {
            "provider": settings.llm_provider,
            "configured": settings.llm_configured,
            "model": settings.llm_model if settings.llm_configured else None,
            "note": None
            if settings.llm_configured
            else "No LLM credentials configured — agents run in deterministic mode.",
        },
        "embeddings": {
            "provider": settings.embeddings_provider,
            "semantic": settings.embeddings_provider in ("voyage", "openai"),
            "note": (
                "local-hash is a deterministic lexical embedding for offline use, "
                "not a semantic model."
                if settings.embeddings_provider == "local-hash"
                else None
            ),
        },
        "vector_search": {
            "pgvector": caps.pgvector,
            "path": "pgvector" if caps.pgvector else "service-layer cosine fallback",
        },
        "limits": {
            "max_plan_tasks": settings.max_plan_tasks,
            "max_tool_calls_per_task": settings.max_tool_calls_per_task,
            "max_llm_calls_per_run": settings.max_llm_calls_per_run,
            "run_timeout_s": settings.run_timeout_s,
            "task_timeout_s": settings.task_timeout_s,
            "max_review_iterations": settings.max_review_iterations,
        },
        "environment": settings.env,
    }
