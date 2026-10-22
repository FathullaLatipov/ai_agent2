"""Read-only, schema-allowlisted SQL query tool.

The model never submits raw SQL. It submits a structured query spec (table,
columns, filters, limit) which is compiled here into parameterized SQL against an
explicitly approved set of tables/columns, always scoped to the caller's
organization. This is deliberately stricter than accepting SELECT strings: there
is no injection surface and no way to escape tenant scoping.
"""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry

# Approved schema: logical name -> (physical table, org column, allowed columns)
APPROVED_TABLES: dict[str, dict] = {
    "documents": {
        "table": "documents",
        "org_column": "organization_id",
        "columns": ["id", "filename", "content_type", "size_bytes", "status", "created_at"],
    },
    "runs": {
        "table": "agent_runs",
        "org_column": "organization_id",
        "columns": ["id", "title", "status", "mode", "created_at", "started_at", "finished_at"],
    },
    "tool_calls": {
        "table": "tool_calls",
        "org_column": "organization_id",
        "columns": ["id", "tool_name", "agent_key", "status", "duration_ms", "created_at"],
    },
}

_OPERATORS = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


class SqlFilter(BaseModel):
    column: str
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte"] = "eq"
    value: str | int | float | bool


class SqlQueryInput(BaseModel):
    table: str
    columns: list[str] = Field(default_factory=list, max_length=20)
    filters: list[SqlFilter] = Field(default_factory=list, max_length=10)
    order_by: str | None = None
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=50, ge=1, le=500)


class SqlQueryOutput(BaseModel):
    table: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    sql: str  # the exact parameterized SQL that ran — auditable


async def _read_query(ctx: ToolContext, args: SqlQueryInput) -> SqlQueryOutput:
    approved = APPROVED_TABLES.get(args.table)
    if approved is None:
        raise ToolInputError(
            f"table '{args.table}' is not in the approved schema "
            f"(approved: {', '.join(sorted(APPROVED_TABLES))})"
        )
    allowed_cols: list[str] = approved["columns"]
    columns = args.columns or allowed_cols
    for col in columns:
        if col not in allowed_cols:
            raise ToolInputError(f"column '{col}' is not approved for table '{args.table}'")

    where = [f"{approved['org_column']} = :org_id"]
    params: dict = {"org_id": str(ctx.organization_id), "limit": args.limit}
    for i, f in enumerate(args.filters):
        if f.column not in allowed_cols:
            raise ToolInputError(f"filter column '{f.column}' is not approved")
        where.append(f"{f.column} {_OPERATORS[f.op]} :p{i}")
        params[f"p{i}"] = f.value

    order_sql = ""
    if args.order_by:
        if args.order_by not in allowed_cols:
            raise ToolInputError(f"order_by column '{args.order_by}' is not approved")
        order_sql = f" ORDER BY {args.order_by} {'ASC' if args.order == 'asc' else 'DESC'}"

    # Identifiers are drawn exclusively from the APPROVED_TABLES allowlist above;
    # all values are bound parameters — no injection surface.
    sql = (
        f"SELECT {', '.join(columns)} FROM {approved['table']} "  # noqa: S608
        f"WHERE {' AND '.join(where)}{order_sql} LIMIT :limit"
    )
    result = await ctx.session.execute(text(sql), params)
    rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    return SqlQueryOutput(
        table=args.table,
        columns=columns,
        rows=[{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows],
        row_count=len(rows),
        sql=sql,
    )


registry.register(
    ToolSpec(
        name="sql.read_query",
        description=(
            "Run a read-only, parameterized query against the approved schema "
            "(documents, runs, tool_calls), always scoped to your organization."
        ),
        input_model=SqlQueryInput,
        output_model=SqlQueryOutput,
        handler=_read_query,
        timeout_s=15.0,
    )
)
