"""Deterministic CSV dataset tools.

All numeric work happens here in Python — never in an LLM. Datasets are uploaded
documents (text/csv) owned by the caller's organization; access is verified on
every call. Size caps keep parsing bounded.
"""

import asyncio
import csv
import datetime as dt
import statistics
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import Document, DocumentStatus
from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000


async def _load_csv(ctx: ToolContext, document_id: uuid.UUID) -> tuple[Document, list[dict]]:
    doc = await ctx.session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == ctx.organization_id,  # tenant check
        )
    )
    if doc is None:
        raise ToolInputError("dataset not found in this organization")
    if doc.status not in (DocumentStatus.READY, DocumentStatus.PROCESSING):
        raise ToolInputError(f"dataset is not ready (status: {doc.status.value})")
    # File IO and parsing are blocking — run off the event loop.
    rows = await asyncio.to_thread(_read_csv_file, Path(doc.storage_path))
    return doc, rows


def _read_csv_file(path: Path) -> list[dict]:
    if not path.is_file():
        raise ToolInputError("dataset file is missing from storage")
    if path.stat().st_size > MAX_BYTES:
        raise ToolInputError("dataset exceeds the 20 MB processing limit")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise ToolInputError("CSV has no header row")
            rows = []
            for i, row in enumerate(reader):
                if i >= MAX_ROWS:
                    raise ToolInputError(f"dataset exceeds the {MAX_ROWS} row limit")
                rows.append(row)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ToolInputError(f"could not parse CSV: {exc}") from exc
    return rows


def _to_number(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip().replace(",", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y")


def _to_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    v = value.strip()[:19]
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _infer_type(values: list[str | None]) -> str:
    sample = [v for v in values if v and v.strip()][:50]
    if not sample:
        return "empty"
    if all(_to_number(v) is not None for v in sample):
        return "number"
    if all(_to_date(v) is not None for v in sample):
        return "date"
    return "text"


class InspectInput(BaseModel):
    document_id: uuid.UUID


class ColumnInfo(BaseModel):
    name: str
    inferred_type: str
    non_empty: int
    distinct: int


class InspectOutput(BaseModel):
    document_id: uuid.UUID
    filename: str
    row_count: int
    columns: list[ColumnInfo]
    sample_rows: list[dict]
    date_range: dict | None = None


async def _inspect(ctx: ToolContext, args: InspectInput) -> InspectOutput:
    doc, rows = await _load_csv(ctx, args.document_id)
    fieldnames = list(rows[0].keys()) if rows else []
    columns = []
    date_range = None
    for name in fieldnames:
        values = [r.get(name) for r in rows]
        inferred = _infer_type(values)
        columns.append(
            ColumnInfo(
                name=name,
                inferred_type=inferred,
                non_empty=sum(1 for v in values if v and v.strip()),
                distinct=len({v for v in values if v}),
            )
        )
        if inferred == "date" and date_range is None:
            dates = [d for d in (_to_date(v) for v in values) if d]
            if dates:
                date_range = {
                    "column": name,
                    "min": min(dates).isoformat(),
                    "max": max(dates).isoformat(),
                }
    return InspectOutput(
        document_id=doc.id,
        filename=doc.filename,
        row_count=len(rows),
        columns=columns,
        sample_rows=[dict(r) for r in rows[:5]],
        date_range=date_range,
    )


class StatsInput(BaseModel):
    document_id: uuid.UUID
    columns: list[str] = Field(default_factory=list, max_length=20)


class ColumnStats(BaseModel):
    column: str
    count: int
    total: float
    mean: float
    median: float
    minimum: float
    maximum: float


class StatsOutput(BaseModel):
    document_id: uuid.UUID
    stats: list[ColumnStats]
    skipped_non_numeric: list[str] = Field(default_factory=list)


async def _stats(ctx: ToolContext, args: StatsInput) -> StatsOutput:
    _doc, rows = await _load_csv(ctx, args.document_id)
    fieldnames = list(rows[0].keys()) if rows else []
    targets = args.columns or fieldnames
    stats_out, skipped = [], []
    for name in targets:
        if name not in fieldnames:
            raise ToolInputError(f"column not found: {name}")
        numbers = [n for n in (_to_number(r.get(name)) for r in rows) if n is not None]
        if not numbers:
            skipped.append(name)
            continue
        stats_out.append(
            ColumnStats(
                column=name,
                count=len(numbers),
                total=round(sum(numbers), 6),
                mean=round(statistics.fmean(numbers), 6),
                median=round(statistics.median(numbers), 6),
                minimum=min(numbers),
                maximum=max(numbers),
            )
        )
    return StatsOutput(document_id=args.document_id, stats=stats_out, skipped_non_numeric=skipped)


class AggregateInput(BaseModel):
    document_id: uuid.UUID
    group_by: str
    value_column: str
    agg: Literal["sum", "mean", "count"] = "sum"
    date_column: str | None = None
    last_days: int | None = Field(default=None, ge=1, le=3650)
    sort: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=50, ge=1, le=500)


class AggregateRow(BaseModel):
    group: str
    value: float
    rows: int


class AggregateOutput(BaseModel):
    document_id: uuid.UUID
    group_by: str
    value_column: str
    agg: str
    rows: list[AggregateRow]
    window: dict | None = None
    note: str = ""


async def _aggregate(ctx: ToolContext, args: AggregateInput) -> AggregateOutput:
    _doc, rows = await _load_csv(ctx, args.document_id)
    fieldnames = list(rows[0].keys()) if rows else []
    for col in (args.group_by, args.value_column):
        if col not in fieldnames:
            raise ToolInputError(f"column not found: {col}")

    window = None
    note = ""
    if args.last_days:
        if not args.date_column:
            raise ToolInputError("last_days requires date_column")
        if args.date_column not in fieldnames:
            raise ToolInputError(f"column not found: {args.date_column}")
        dates = [d for d in (_to_date(r.get(args.date_column)) for r in rows) if d]
        if not dates:
            raise ToolInputError(f"no parseable dates in column {args.date_column}")
        # Window is anchored to the dataset's latest date, so results are
        # deterministic for a fixed dataset. This is stated in the output.
        anchor = max(dates)
        since = anchor - dt.timedelta(days=args.last_days - 1)
        rows = [
            r
            for r in rows
            if (d := _to_date(r.get(args.date_column))) is not None and since <= d <= anchor
        ]
        window = {"column": args.date_column, "from": since.isoformat(), "to": anchor.isoformat()}
        note = (
            f"window anchored to the dataset's latest date ({anchor.isoformat()}), "
            f"covering {args.last_days} days"
        )

    groups: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for r in rows:
        key = (r.get(args.group_by) or "").strip() or "(empty)"
        counts[key] = counts.get(key, 0) + 1
        num = _to_number(r.get(args.value_column))
        if num is not None:
            groups.setdefault(key, []).append(num)

    out_rows = []
    for key, count in counts.items():
        numbers = groups.get(key, [])
        if args.agg == "count":
            value = float(count)
        elif args.agg == "mean":
            value = round(statistics.fmean(numbers), 6) if numbers else 0.0
        else:
            value = round(sum(numbers), 6)
        out_rows.append(AggregateRow(group=key, value=value, rows=count))

    out_rows.sort(key=lambda r: r.value, reverse=(args.sort == "desc"))
    return AggregateOutput(
        document_id=args.document_id,
        group_by=args.group_by,
        value_column=args.value_column,
        agg=args.agg,
        rows=out_rows[: args.limit],
        window=window,
        note=note,
    )


registry.register(
    ToolSpec(
        name="dataset.inspect",
        description="Inspect an uploaded CSV dataset: columns, types, row count, sample.",
        input_model=InspectInput,
        output_model=InspectOutput,
        handler=_inspect,
        timeout_s=30.0,
    )
)
registry.register(
    ToolSpec(
        name="dataset.stats",
        description="Compute count/sum/mean/median/min/max for numeric columns of a CSV dataset.",
        input_model=StatsInput,
        output_model=StatsOutput,
        handler=_stats,
        timeout_s=30.0,
    )
)
registry.register(
    ToolSpec(
        name="dataset.aggregate",
        description=(
            "Group a CSV dataset by a column and aggregate a value column "
            "(sum/mean/count), optionally restricted to the last N days of a date column."
        ),
        input_model=AggregateInput,
        output_model=AggregateOutput,
        handler=_aggregate,
        timeout_s=30.0,
    )
)
