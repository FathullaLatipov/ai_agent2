"""Tenant-scoped chunk retrieval.

Two real search paths, reported honestly in every result:
  - "semantic-pgvector": embeddings + pgvector cosine SQL (extension present)
  - "semantic-fallback": embeddings + service-layer cosine (no pgvector)
If the query cannot be embedded (provider unavailable/unconfigured mismatch),
retrieval degrades to "lexical" term-overlap ranking — stated in the result,
never silently pretending to be semantic.
"""

import math
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.capabilities import get_capabilities
from app.models import Document, DocumentChunk, DocumentStatus

logger = get_logger("agentos.rag")


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    ordinal: int
    score: float
    method: str
    text: str


@dataclass
class RetrievalResult:
    hits: list[RetrievedChunk] = field(default_factory=list)
    method: str = "lexical"
    note: str = ""


_WORD = re.compile(r"[a-zA-Z0-9]{2,}")


def _tokens(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s)]


def _lexical_score(query_tokens: set[str], chunk_text: str) -> float:
    chunk_tokens = _tokens(chunk_text)
    if not chunk_tokens:
        return 0.0
    hits = sum(1 for t in chunk_tokens if t in query_tokens)
    return hits / math.sqrt(len(chunk_tokens))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def search_chunks(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    query: str,
    top_k: int = 5,
) -> RetrievalResult:
    """Search READY documents belonging to `organization_id` only."""
    from app.rag.embeddings import embed_query_safe  # late import: provider config

    query_vector, provider_note = await embed_query_safe(query)

    if query_vector is not None and get_capabilities().pgvector:
        return await _pgvector_search(
            session, organization_id, query, query_vector, top_k, provider_note
        )

    # Load org chunks of ready documents (bounded) for in-service scoring.
    rows = (
        await session.execute(
            select(DocumentChunk, Document.filename)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.organization_id == organization_id,
                Document.status == DocumentStatus.READY,
            )
            .limit(5000)
        )
    ).all()

    if query_vector is not None:
        scored = [
            (cosine(query_vector, chunk.embedding_json), chunk, filename)
            for chunk, filename in rows
            if chunk.embedding_json
        ]
        method = "semantic-fallback"
        note = provider_note + " pgvector unavailable — cosine computed in the service layer."
    else:
        q_tokens = set(_tokens(query))
        scored = [
            (_lexical_score(q_tokens, chunk.text), chunk, filename) for chunk, filename in rows
        ]
        method = "lexical"
        note = provider_note + " Ranking by term overlap."

    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: s[0], reverse=True)
    return RetrievalResult(
        hits=[
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                filename=filename,
                ordinal=chunk.ordinal,
                score=float(score),
                method=method,
                text=chunk.text,
            )
            for score, chunk, filename in scored[:top_k]
        ],
        method=method,
        note=note.strip(),
    )


async def _pgvector_search(
    session: AsyncSession,
    organization_id: uuid.UUID,
    query: str,
    query_vector: list[float],
    top_k: int,
    provider_note: str,
) -> RetrievalResult:
    vector_literal = "[" + ",".join(f"{v:.8f}" for v in query_vector) + "]"
    rows = (
        await session.execute(
            text(
                """
                SELECT dc.id, dc.document_id, d.filename, dc.ordinal, dc.text,
                       1 - (dc.embedding <=> CAST(:qv AS vector)) AS score
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.organization_id = :org
                  AND d.status = 'ready'
                  AND dc.embedding IS NOT NULL
                ORDER BY dc.embedding <=> CAST(:qv AS vector)
                LIMIT :k
                """
            ),
            {"qv": vector_literal, "org": str(organization_id), "k": top_k},
        )
    ).all()
    return RetrievalResult(
        hits=[
            RetrievedChunk(
                chunk_id=r[0],
                document_id=r[1],
                filename=r[2],
                ordinal=r[3],
                score=float(r[5]),
                method="semantic-pgvector",
                text=r[4],
            )
            for r in rows
        ],
        method="semantic-pgvector",
        note=provider_note.strip(),
    )
