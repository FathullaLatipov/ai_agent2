"""Data Analyst agent — deterministic CSV metrics via dataset tools.

The LLM (when configured) only writes the narrative around numbers that were
computed by tools; it never produces numbers itself.
"""

import re

from app.engine.context import AgentContext
from app.engine.errors import TaskFailedError
from app.llm.base import wrap_untrusted
from app.schemas.agents import AnalysisResult, EvidenceRef, MetricValue

ANALYST_SYSTEM = """You are the narrative writer for a data analysis.
You are given metrics that were computed deterministically by tools.
Write summary/method/limitations text around them. Do not compute new numbers,
do not alter values, do not speculate beyond the data. The metrics/tables you
output will be replaced by the platform's computed values.

A reviewer will check your text for rigor, so preserve every methodological
fact from the input — never drop or vague them:
- state the exact analysis window/period; if the aggregation covered the full
  dataset rather than a requested window, say so explicitly in summary AND
  limitations;
- restate the underperformer threshold and exactly how it is derived (50% of
  the mean share across groups), with the numeric value;
- name the data sources: the computed_by tool calls and the dataset the
  metrics came from.
Write in the same language as the task goal when one is given."""

_GROUP_CANDIDATES = ("product", "product_name", "item", "sku", "category", "name")
_VALUE_CANDIDATES = ("revenue", "total", "amount", "sales", "value", "price")

# "за последние 30 дней", "last 30 days", "30 дней", "30 days", "30 суток"
_LAST_DAYS_RE = re.compile(r"(\d{1,4})\s*(?:дн\w*|day\w*|сут\w*)", re.IGNORECASE)


def _infer_last_days(text: str | None) -> int | None:
    """Deterministic fallback when the plan did not pass last_days explicitly:
    read the analysis window straight from the task wording (ru/en)."""
    if not text:
        return None
    match = _LAST_DAYS_RE.search(text)
    if not match:
        return None
    days = int(match.group(1))
    return days if 0 < days <= 3660 else None


def _pick_column(available: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in available}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    for cand in candidates:
        for low, original in lowered.items():
            if cand in low:
                return original
    return None


async def _latest_ready_csv(ctx: AgentContext) -> str | None:
    """Deterministic auto-detect (mirrors the run planner): the organization's
    most recently uploaded READY CSV dataset."""
    from sqlalchemy import select

    from app.models import Document, DocumentStatus

    doc_id = await ctx.session.scalar(
        select(Document.id)
        .where(
            Document.organization_id == ctx.run.organization_id,
            Document.status == DocumentStatus.READY,
            Document.content_type == "text/csv",
        )
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    return str(doc_id) if doc_id else None


async def run_analyst(ctx: AgentContext) -> AnalysisResult:
    params = ctx.task.input
    dataset_id = params.get("dataset_document_id") or await _latest_ready_csv(ctx)
    if not dataset_id:
        raise TaskFailedError(
            "no dataset available: pass dataset_document_id or upload a ready CSV "
            "to the knowledge base"
        )

    inspect = await ctx.call_tool("dataset.inspect", {"document_id": dataset_id})
    info = inspect.output
    columns = [c.name for c in info.columns]  # type: ignore[attr-defined]

    group_by = params.get("group_by") or _pick_column(columns, _GROUP_CANDIDATES)
    value_col = params.get("value_column") or _pick_column(columns, _VALUE_CANDIDATES)
    date_col = params.get("date_column") or (
        info.date_range["column"] if info.date_range else None  # type: ignore[attr-defined]
    )
    if not group_by or not value_col:
        raise TaskFailedError(
            f"could not identify grouping/value columns in {columns}; "
            "pass group_by and value_column params"
        )

    stats = await ctx.call_tool(
        "dataset.stats", {"document_id": dataset_id, "columns": [value_col]}
    )
    agg_args: dict = {
        "document_id": dataset_id,
        "group_by": group_by,
        "value_column": value_col,
        "agg": "sum",
        "sort": "desc",
        "limit": 100,
    }
    last_days = params.get("last_days") or _infer_last_days(ctx.run.input_text)
    if last_days and date_col:
        agg_args.update({"date_column": date_col, "last_days": int(last_days)})
    aggregate = await ctx.call_tool("dataset.aggregate", agg_args)
    agg_out = aggregate.output

    rows = agg_out.rows  # type: ignore[attr-defined]
    total = round(sum(r.value for r in rows), 2)
    metrics: list[MetricValue] = [
        MetricValue(
            name=f"total_{value_col}",
            value=total,
            computed_by=str(aggregate.tool_call_id),
        ),
        MetricValue(
            name="group_count",
            value=len(rows),
            computed_by=str(aggregate.tool_call_id),
        ),
    ]

    # Underperformer criterion (explicit): groups whose share of the total is
    # below half the mean share across groups.
    underperformers: list[str] = []
    if rows and total > 0:
        mean_share = 1.0 / len(rows)
        threshold = 0.5 * mean_share
        for r in rows:
            share = r.value / total
            if share < threshold:
                underperformers.append(f"{r.group} ({value_col}={r.value}, share={share:.1%})")
        metrics.append(
            MetricValue(
                name="underperformer_threshold_share",
                value=round(threshold, 6),
                computed_by=str(aggregate.tool_call_id),
            )
        )

    window_note = agg_out.note or "full dataset (no date window)"  # type: ignore[attr-defined]
    table = {
        "title": f"{value_col} by {group_by}",
        "window": agg_out.window,  # type: ignore[attr-defined]
        "rows": [{"group": r.group, "value": r.value, "rows": r.rows} for r in rows[:25]],
    }
    evidence = [
        EvidenceRef(kind="tool_call", ref_id=str(inspect.tool_call_id), label="dataset.inspect"),
        EvidenceRef(kind="tool_call", ref_id=str(stats.tool_call_id), label="dataset.stats"),
        EvidenceRef(
            kind="tool_call", ref_id=str(aggregate.tool_call_id), label="dataset.aggregate"
        ),
    ]
    method = (
        f"Grouped '{value_col}' by '{group_by}' (sum) over {window_note}. "
        "Underperformers: groups with a share below 50% of the mean share. "
        "All values computed by deterministic dataset tools."
    )
    limitations = [
        "Analysis window is anchored to the dataset's latest date, not today's date.",
        f"Rows with non-numeric '{value_col}' values are excluded from sums.",
    ]
    deterministic_summary = (
        f"Total {value_col}: {total} across {len(rows)} {group_by} groups. "
        + (
            f"Underperforming ({len(underperformers)}): {'; '.join(underperformers[:5])}."
            if underperformers
            else "No groups fell below the underperformance threshold."
        )
    )

    result = AnalysisResult(
        summary=deterministic_summary,
        metrics=metrics,
        tables=[table],
        method=method,
        limitations=limitations,
        evidence=evidence,
    )

    if not ctx.deterministic:
        narrative = await ctx.llm_structured(
            system=ANALYST_SYSTEM,
            user=(
                wrap_untrusted("task_goal", ctx.run.input_text)
                + "\n"
                + wrap_untrusted("computed_analysis", result.model_dump_json())
            ),
            schema=AnalysisResult,
            max_tokens=1500,
        )
        assert isinstance(narrative, AnalysisResult)
        # Keep computed values authoritative; adopt only the narrative fields.
        result.summary = narrative.summary or result.summary
        result.method = narrative.method or result.method
        result.limitations = narrative.limitations or result.limitations
    return result
