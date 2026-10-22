"""CSV dataset tools: deterministic math, date windows, tenant checks."""

import pytest

import app.tools.builtin  # noqa: F401
from app.tools.base import ToolContext, ToolInputError
from app.tools.registry import registry
from tests.helpers import make_org, seed_csv_document, write_sales_csv

ALLOWED = ["dataset.inspect", "dataset.stats", "dataset.aggregate"]


def _ctx(session, org_id) -> ToolContext:
    return ToolContext(
        session=session, organization_id=org_id, run_id=None, task_id=None, agent_key="analyst"
    )


@pytest.fixture
async def sales_doc(db_session, tmp_path):
    org, _user = await make_org(db_session)
    csv_path = tmp_path / "sales.csv"
    write_sales_csv(csv_path)
    doc = await seed_csv_document(db_session, org.id, csv_path)
    return org, doc


async def test_inspect_detects_columns_and_dates(db_session, sales_doc):
    org, doc = sales_doc
    result = await registry.execute(
        _ctx(db_session, org.id),
        tool_name="dataset.inspect",
        arguments={"document_id": str(doc.id)},
        allowed_tools=ALLOWED,
    )
    out = result.output
    assert out.row_count == 45 * 4
    names = {c.name: c.inferred_type for c in out.columns}
    assert names["revenue"] == "number"
    assert names["date"] == "date"
    assert out.date_range["max"] == "2026-10-15"


async def test_stats_computes_exact_values(db_session, sales_doc):
    org, doc = sales_doc
    result = await registry.execute(
        _ctx(db_session, org.id),
        tool_name="dataset.stats",
        arguments={"document_id": str(doc.id), "columns": ["revenue"]},
        allowed_tools=ALLOWED,
    )
    stats = result.output.stats[0]
    # 45 days * (1000 + 900 + 1100 + 12.5)
    assert stats.total == 45 * 3012.5
    assert stats.count == 180
    assert stats.minimum == 12.5
    assert stats.maximum == 1100.0


async def test_aggregate_with_30_day_window(db_session, sales_doc):
    org, doc = sales_doc
    result = await registry.execute(
        _ctx(db_session, org.id),
        tool_name="dataset.aggregate",
        arguments={
            "document_id": str(doc.id),
            "group_by": "product",
            "value_column": "revenue",
            "agg": "sum",
            "date_column": "date",
            "last_days": 30,
        },
        allowed_tools=ALLOWED,
    )
    out = result.output
    assert out.window == {"column": "date", "from": "2026-09-16", "to": "2026-10-15"}
    values = {r.group: r.value for r in out.rows}
    assert values["Alpha Widget"] == 30 * 1000.0
    assert values["Delta Widget"] == 30 * 12.5
    assert out.rows[0].group == "Gamma Widget"  # sorted desc


async def test_dataset_cross_tenant_access_blocked(db_session, sales_doc):
    _org_a, doc = sales_doc
    org_b, _ = await make_org(db_session, slug="otherorg")
    with pytest.raises(ToolInputError, match="not found"):
        await registry.execute(
            _ctx(db_session, org_b.id),
            tool_name="dataset.inspect",
            arguments={"document_id": str(doc.id)},
            allowed_tools=ALLOWED,
        )


async def test_unknown_column_rejected(db_session, sales_doc):
    org, doc = sales_doc
    with pytest.raises(ToolInputError, match="column not found"):
        await registry.execute(
            _ctx(db_session, org.id),
            tool_name="dataset.aggregate",
            arguments={
                "document_id": str(doc.id),
                "group_by": "nonexistent",
                "value_column": "revenue",
            },
            allowed_tools=ALLOWED,
        )
