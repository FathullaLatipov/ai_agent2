"""Conditionally enable pgvector and add the embedding column.

On PostgreSQL installations without the pgvector extension (e.g. default Windows
installers) this migration is a documented no-op: retrieval then uses the
service-layer cosine fallback over document_chunks.embedding_json (ADR-003).

Revision ID: a1c2e3f4b5d6
Revises: 9b5b39613ea6
Create Date: 2026-10-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "9b5b39613ea6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pgvector_available(conn) -> bool:
    return (
        conn.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _pgvector_available(conn):
        print("pgvector extension not available on this server — skipping (fallback path active)")
        return
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(sa.text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding"))
