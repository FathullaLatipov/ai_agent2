"""RAG pipeline: upload API, ingestion, chunking, retrieval, tenant isolation."""

import io

from httpx import AsyncClient

from app.rag.ingestion import chunk_text, normalize_text
from tests.conftest import register_second_org

POLICY_TEXT = """Company Travel Policy

Employees may book economy class flights for trips under six hours.
Business class requires VP approval and applies only to flights over six hours.

Expense reports must be submitted within 30 days of travel completion.
Receipts are required for all expenses above 25 USD.

Remote work is allowed up to three days per week with manager approval.
"""


async def _upload(client: AsyncClient, headers: dict, name: str, content: bytes) -> dict:
    resp = await client.post(
        "/api/v1/documents",
        files={"file": (name, io.BytesIO(content), "application/octet-stream")},
        headers=headers,
    )
    return resp


def test_chunking_is_paragraph_aware_with_overlap():
    text = "\n\n".join(f"Paragraph {i} " + ("x" * 300) for i in range(10))
    chunks = chunk_text(text, size=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    # Full coverage: every paragraph appears in some chunk.
    for i in range(10):
        assert any(f"Paragraph {i} " in c for c in chunks)


def test_normalize_strips_nulls_and_collapses_newlines():
    assert normalize_text("a\x00b\r\n\n\n\n\nc") == "ab\n\n\nc".replace("\n\n\n", "\n\n")


async def test_upload_ingest_and_search_roundtrip(client: AsyncClient, auth_headers: dict):
    resp = await _upload(client, auth_headers, "travel_policy.txt", POLICY_TEXT.encode())
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    # Inline test worker already ingested it.
    assert doc["status"] == "ready"
    assert doc["chunk_count"] >= 1
    assert "local-hash" in doc["embeddings_provider"]

    search = await client.post(
        "/api/v1/documents/search",
        json={"query": "business class flight approval"},
        headers=auth_headers,
    )
    assert search.status_code == 200
    body = search.json()
    assert body["method"] in ("semantic-fallback", "semantic-pgvector")
    assert body["hits"], "expected at least one hit"
    top = body["hits"][0]
    assert top["filename"] == "travel_policy.txt"
    assert "Business class" in top["excerpt"] or "business" in top["excerpt"].lower()


async def test_duplicate_upload_rejected(client: AsyncClient, auth_headers: dict):
    first = await _upload(client, auth_headers, "policy.txt", POLICY_TEXT.encode())
    assert first.status_code == 200
    dup = await _upload(client, auth_headers, "policy-copy.txt", POLICY_TEXT.encode())
    assert dup.status_code == 409
    assert "already uploaded" in dup.json()["detail"]


async def test_unsupported_and_empty_uploads_rejected(client: AsyncClient, auth_headers: dict):
    bad = await _upload(client, auth_headers, "malware.exe", b"MZ...")
    assert bad.status_code == 415
    empty = await _upload(client, auth_headers, "empty.txt", b"")
    assert empty.status_code == 400


async def test_broken_pdf_marks_document_failed(client: AsyncClient, auth_headers: dict):
    resp = await _upload(client, auth_headers, "broken.pdf", b"%PDF-1.4 garbage content")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["status"] == "failed"
    assert doc["error"]


async def test_csv_becomes_ready_dataset_without_chunks(
    client: AsyncClient, auth_headers: dict
):
    resp = await _upload(client, auth_headers, "data.csv", b"a,b\n1,2\n3,4\n")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 0


async def test_document_tenant_isolation(client: AsyncClient, auth_headers: dict):
    resp = await _upload(client, auth_headers, "secret_policy.txt", POLICY_TEXT.encode())
    doc_id = resp.json()["id"]
    other = await register_second_org(client)

    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=other)).status_code == 404
    assert (await client.get("/api/v1/documents", headers=other)).json() == []
    # Cross-tenant search finds nothing.
    search = await client.post(
        "/api/v1/documents/search",
        json={"query": "business class flight approval"},
        headers=other,
    )
    assert search.json()["hits"] == []
    # Deleting another org's document is a 404, and the original remains.
    assert (await client.delete(f"/api/v1/documents/{doc_id}", headers=other)).status_code == 404
    assert (
        await client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
    ).status_code == 200


async def test_research_run_grounded_in_uploaded_document(
    client: AsyncClient, auth_headers: dict
):
    """Scenario B: a question answered from an uploaded policy with source refs."""
    await _upload(client, auth_headers, "travel_policy.txt", POLICY_TEXT.encode())
    run = await client.post(
        "/api/v1/runs",
        json={"input_text": "What is the policy for business class flights?"},
        headers=auth_headers,
    )
    detail = (
        await client.get(f"/api/v1/runs/{run.json()['id']}", headers=auth_headers)
    ).json()
    assert detail["status"] == "completed"
    research = detail["tasks"][0]["output"]
    assert research["evidence"], "expected grounded evidence references"
    assert all(ev["kind"] == "document_chunk" for ev in research["evidence"])
    assert any("travel_policy.txt" in ev["label"] for ev in research["evidence"])
    assert not research["missing_information"]
    report = detail["report"]["body_markdown"]
    assert "travel_policy.txt" in report  # evidence appendix cites the source
