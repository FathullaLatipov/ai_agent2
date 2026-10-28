"""Organization invites (one-time join links with a fixed role).

Revision ID: d4e5f6a7b8c9
Revises: b36c90cc92cf
Create Date: 2026-10-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "b36c90cc92cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "invited_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("token_hash", name="uq_org_invite_token"),
    )
    op.create_index("ix_org_invites_org", "organization_invites", ["organization_id"])

    # Same dormant RLS second layer as the other tenant tables (c7d8e9f0a1b2).
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE organization_invites ENABLE ROW LEVEL SECURITY"))
    conn.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_organization_invites ON organization_invites
            USING (
                organization_id = current_setting('app.current_org', true)::uuid
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DROP POLICY IF EXISTS tenant_isolation_organization_invites "
            "ON organization_invites"
        )
    )
    op.drop_index("ix_org_invites_org", table_name="organization_invites")
    op.drop_table("organization_invites")
