"""Knowledge-base tools.

kb.search delegates to the retrieval service, which uses semantic search when an
embeddings provider + pgvector/fallback path are configured, and honest lexical
ranking otherwise. Results always carry real document/chunk references.
"""

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import Document, DocumentChunk, DocumentStatus
from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry


class KbSearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class KbSearchHit(BaseModel):
    document_id: uuid.UUID
    chunk_id: uuid.UUID
    filename: str
    ordinal: int
    score: float
    method: str
    excerpt: str


class KbSearchOutput(BaseModel):
    query: str
    hits: list[KbSearchHit]
    method: str
    note: str = ""


async def _kb_search(ctx: ToolContext, args: KbSearchInput) -> KbSearchOutput:
    from app.rag.retrieval import search_chunks  # local import to avoid cycles

    results = await search_chunks(
        ctx.session,
        organization_id=ctx.organization_id,
        query=args.query,
        top_k=args.top_k,
    )
    return KbSearchOutput(
        query=args.query,
        hits=[
            KbSearchHit(
                document_id=r.document_id,
                chunk_id=r.chunk_id,
                filename=r.filename,
                ordinal=r.ordinal,
                score=round(r.score, 6),
                method=r.method,
                excerpt=r.text[:500],
            )
            for r in results.hits
        ],
        method=results.method,
        note=results.note,
    )


class KbGetDocumentInput(BaseModel):
    document_id: uuid.UUID
    max_chunks: int = Field(default=10, ge=1, le=50)


class KbGetDocumentOutput(BaseModel):
    document_id: uuid.UUID
    filename: str
    status: str
    chunk_count: int
    chunks: list[dict]


async def _kb_get_document(ctx: ToolContext, args: KbGetDocumentInput) -> KbGetDocumentOutput:
    doc = await ctx.session.scalar(
        select(Document).where(
            Document.id == args.document_id,
            Document.organization_id == ctx.organization_id,  # tenant check
        )
    )
    if doc is None:
        raise ToolInputError("document not found in this organization")
    chunks = (
        await ctx.session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.ordinal)
            .limit(args.max_chunks)
        )
    ).all()
    return KbGetDocumentOutput(
        document_id=doc.id,
        filename=doc.filename,
        status=doc.status.value if isinstance(doc.status, DocumentStatus) else str(doc.status),
        chunk_count=doc.chunk_count,
        chunks=[
            {"chunk_id": str(c.id), "ordinal": c.ordinal, "text": c.text[:1500]} for c in chunks
        ],
    )


registry.register(
    ToolSpec(
        name="kb.search",
        description="Search the organization's knowledge base; returns scored chunks with sources.",
        input_model=KbSearchInput,
        output_model=KbSearchOutput,
        handler=_kb_search,
        timeout_s=20.0,
    )
)
registry.register(
    ToolSpec(
        name="kb.get_document",
        description="Fetch a knowledge-base document's metadata and leading chunks.",
        input_model=KbGetDocumentInput,
        output_model=KbGetDocumentOutput,
        handler=_kb_get_document,
        timeout_s=15.0,
    )
)
