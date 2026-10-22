"""Documents and chunks for the knowledge base.

The pgvector `embedding` column is added by a conditional migration only when the
extension is available; the ORM always carries `embedding_json` so the fallback
search path works on any PostgreSQL. See docs/architecture.md ADR-003.
"""

import uuid

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentStatus
from app.models.runs import _enum


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "sha256", name="uq_document_org_sha"),
        Index("ix_documents_org", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), nullable=False, default=DocumentStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embeddings_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_doc_ordinal"),
        Index("ix_chunks_org", "organization_id"),
        Index("ix_chunks_document", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Fallback representation; the pgvector `embedding` column is migration-managed.
    embedding_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
