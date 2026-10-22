"""Row-Level Security policies (defense in depth).

Enables RLS on tenant-owned tables with a policy requiring
    organization_id = current_setting('app.current_org')::uuid

Activation model (documented in docs/security.md):
- The PRIMARY tenant-isolation control is service-layer scoping (every query
  filters by organization_id) — covered by tests.
- These policies are a dormant second layer: they bind only when the app
  connects as a NON-OWNER role (e.g. `agentos_app`, see scripts/provision_rls_role.sql)
  and sets `app.current_org` per transaction. Connecting as the table owner
  (local dev `postgres`) bypasses RLS by PostgreSQL semantics.

Revision ID: c7d8e9f0a1b2
Revises: 58415d295e81
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "58415d295e81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "projects",
    "agent_runs",
    "run_tasks",
    "execution_events",
    "tool_calls",
    "approval_requests",
    "conversations",
    "documents",
    "document_chunks",
    "reports",
]


def upgrade() -> None:
    conn = op.get_bind()
    for table in TENANT_TABLES:
        conn.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation_{table} ON {table}
                USING (
                    organization_id = current_setting('app.current_org', true)::uuid
                )
                """
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table in TENANT_TABLES:
        conn.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
        conn.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
