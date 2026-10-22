"""Document ingestion pipeline.

upload (API) → pending → [worker] processing → extract → normalize → chunk →
embed → persist chunks (+ pgvector column when available) → ready.
Any failure marks the document `failed` with the reason — never a fake "ready".

CSV documents are datasets, not prose: they become `ready` without chunks and are
consumed by the dataset tools directly.
"""

import asyncio
import re
import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy import text as sa_text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.capabilities import detect_capabilities
from app.db.session import get_engine, get_sessionmaker
from app.models import Document, DocumentChunk, DocumentStatus
from app.rag.embeddings import EmbeddingError, embed_texts

logger = get_logger("agentos.ingestion")

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MAX_CHUNKS = 2000


class ExtractionError(Exception):
    pass


def _extract_text(path: Path, content_type: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in (".txt", ".md") or content_type.startswith("text/"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ExtractionError(f"could not read file: {exc}") from exc
    if suffix == ".pdf" or content_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise ExtractionError(f"PDF parsing failed: {exc}") from exc
        text = "\n\n".join(pages).strip()
        if not text:
            raise ExtractionError(
                "PDF contains no extractable text (scanned PDFs need OCR, which is not enabled)"
            )
        return text
    raise ExtractionError(f"unsupported document type: {suffix or content_type}")


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware sliding window."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}" if tail else paragraph
        while len(current) > size:  # oversized single paragraph: hard split
            chunks.append(current[:size])
            current = current[size - overlap :]
    if current.strip():
        chunks.append(current)
    return chunks[:MAX_CHUNKS]


def _is_dataset(doc: Document) -> bool:
    return doc.content_type == "text/csv" or doc.filename.lower().endswith(".csv")


async def ingest_document(document_id: str) -> str:
    sessionmaker = get_sessionmaker()
    doc_uuid = uuid.UUID(document_id)

    async with sessionmaker() as session:
        doc = await session.get(Document, doc_uuid)
        if doc is None:
            return "not_found"
        if doc.status == DocumentStatus.READY:
            return "already_ready"
        doc.status = DocumentStatus.PROCESSING
        doc.error = None
        await session.commit()

        try:
            if _is_dataset(doc):
                # Datasets skip chunking/embedding; the dataset tools parse them.
                doc.status = DocumentStatus.READY
                doc.chunk_count = 0
                doc.embeddings_provider = None
                await session.commit()
                return "ready_dataset"

            raw = await asyncio.to_thread(
                _extract_text, Path(doc.storage_path), doc.content_type, doc.filename
            )
            normalized = normalize_text(raw)
            if not normalized:
                raise ExtractionError("document contains no text")
            chunks = chunk_text(normalized)
            vectors = await embed_texts(chunks, input_type="document")

            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            chunk_rows = [
                DocumentChunk(
                    document_id=doc.id,
                    organization_id=doc.organization_id,
                    ordinal=i,
                    text=chunk,
                    meta={"filename": doc.filename},
                    embedding_json=vector,
                )
                for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
            ]
            session.add_all(chunk_rows)
            await session.flush()

            caps = await detect_capabilities(get_engine())
            if caps.pgvector:
                for row in chunk_rows:
                    vector_literal = (
                        "[" + ",".join(f"{v:.8f}" for v in row.embedding_json) + "]"
                    )
                    await session.execute(
                        sa_text(
                            "UPDATE document_chunks SET embedding = CAST(:v AS vector) "
                            "WHERE id = :id"
                        ),
                        {"v": vector_literal, "id": str(row.id)},
                    )

            doc.status = DocumentStatus.READY
            doc.chunk_count = len(chunk_rows)
            doc.embeddings_provider = get_settings().embeddings_provider
            await session.commit()
            return "ready"
        except (ExtractionError, EmbeddingError) as exc:
            await session.rollback()
            doc = await session.get(Document, doc_uuid)
            doc.status = DocumentStatus.FAILED
            doc.error = str(exc)[:1000]
            await session.commit()
            logger.warning("ingestion failed", document_id=document_id, error=str(exc))
            return "failed"
        except Exception:
            await session.rollback()
            doc = await session.get(Document, doc_uuid)
            doc.status = DocumentStatus.FAILED
            doc.error = "internal ingestion error"
            await session.commit()
            logger.exception("ingestion crashed", document_id=document_id)
            return "failed"
