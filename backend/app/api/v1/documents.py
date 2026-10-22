"""Knowledge-base document endpoints: upload, status, search, delete."""

import datetime as dt
import hashlib
import uuid
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import OrgCtx, SessionDep, rate_limit, require_role
from app.models import ActorType, Document, DocumentChunk, DocumentStatus, MemberRole
from app.services.audit import record_audit

router = APIRouter(prefix="/documents", tags=["documents"])

_member = require_role(MemberRole.MEMBER)

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf"}
_EXTENSION_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
}


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    error: str | None
    chunk_count: int
    embeddings_provider: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class DocumentDetailOut(DocumentOut):
    chunks_preview: list[dict] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post(
    "",
    response_model=DocumentOut,
    dependencies=[Depends(rate_limit("uploads", 20))],
)
async def upload_document(
    file: UploadFile, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    settings = get_settings()
    original_name = Path(file.filename or "upload").name[:200]
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported file type {suffix or '(none)'}; allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file exceeds {settings.max_upload_bytes} bytes",
        )
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")

    sha256 = hashlib.sha256(content).hexdigest()
    duplicate = await session.scalar(
        select(Document).where(
            Document.organization_id == ctx.organization.id, Document.sha256 == sha256
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"identical document already uploaded: {duplicate.filename} ({duplicate.id})",
        )

    store_dir = Path(settings.upload_dir) / str(ctx.organization.id)
    store_dir.mkdir(parents=True, exist_ok=True)
    storage_path = store_dir / f"{uuid.uuid4().hex}{suffix}"
    async with await anyio.open_file(storage_path, "wb") as fh:
        await fh.write(content)

    doc = Document(
        organization_id=ctx.organization.id,
        uploaded_by=ctx.user.id,
        filename=original_name,
        content_type=_EXTENSION_CONTENT_TYPES[suffix],
        size_bytes=len(content),
        sha256=sha256,
        storage_path=str(storage_path),
        status=DocumentStatus.PENDING,
    )
    session.add(doc)
    await session.flush()
    await record_audit(
        session,
        action="document.uploaded",
        actor_type=ActorType.USER,
        actor_id=str(ctx.user.id),
        organization_id=ctx.organization.id,
        entity_type="document",
        entity_id=str(doc.id),
        meta={"filename": original_name, "size": len(content)},
    )
    await session.commit()

    from app.services.queueing import QueueUnavailableError, enqueue
    from app.worker.tasks import ingest_document_task

    try:
        await enqueue(ingest_document_task, str(doc.id))
    except QueueUnavailableError:
        # The file is stored but ingestion could not be scheduled — record an
        # honest failed state instead of an eternally 'pending' orphan.
        doc.status = DocumentStatus.FAILED
        doc.error = (
            "ingestion queue unavailable — delete this document and re-upload "
            "once the queue is reachable"
        )
        await session.commit()
        await session.refresh(doc)
        return DocumentOut.model_validate(doc)
    # In test/inline mode ingestion already ran — return fresh state.
    await session.refresh(doc)
    return DocumentOut.model_validate(doc)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session: SessionDep,
    ctx: OrgCtx,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    query = select(Document).where(Document.organization_id == ctx.organization.id)
    if status_filter:
        try:
            query = query.where(Document.status == DocumentStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown status") from exc
    docs = (await session.scalars(query.order_by(Document.created_at.desc()))).all()
    return [DocumentOut.model_validate(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentDetailOut)
async def get_document(document_id: uuid.UUID, session: SessionDep, ctx: OrgCtx):
    doc = await session.scalar(
        select(Document).where(
            Document.id == document_id, Document.organization_id == ctx.organization.id
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    chunks = (
        await session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.ordinal)
            .limit(5)
        )
    ).all()
    out = DocumentDetailOut.model_validate(doc, from_attributes=True)
    out.chunks_preview = [{"ordinal": c.ordinal, "text": c.text[:400]} for c in chunks]
    return out


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, session: SessionDep, ctx: Annotated[OrgCtx, Depends(_member)]
):
    doc = await session.scalar(
        select(Document).where(
            Document.id == document_id, Document.organization_id == ctx.organization.id
        )
    )
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    try:
        await anyio.Path(doc.storage_path).unlink(missing_ok=True)
    except OSError:
        pass  # row removal below still makes the document unreachable
    await session.delete(doc)
    await record_audit(
        session,
        action="document.deleted",
        actor_type=ActorType.USER,
        actor_id=str(ctx.user.id),
        organization_id=ctx.organization.id,
        entity_type="document",
        entity_id=str(document_id),
    )
    await session.commit()


@router.post("/search")
async def search_documents(body: SearchRequest, session: SessionDep, ctx: OrgCtx) -> dict:
    from app.rag.retrieval import search_chunks

    result = await search_chunks(
        session, organization_id=ctx.organization.id, query=body.query, top_k=body.top_k
    )
    return {
        "query": body.query,
        "method": result.method,
        "note": result.note,
        "hits": [
            {
                "document_id": str(h.document_id),
                "chunk_id": str(h.chunk_id),
                "filename": h.filename,
                "ordinal": h.ordinal,
                "score": round(h.score, 6),
                "excerpt": h.text[:400],
            }
            for h in result.hits
        ],
    }
