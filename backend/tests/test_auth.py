from httpx import AsyncClient

from tests.conftest import REGISTER_BODY


async def test_register_and_me(client: AsyncClient):
    resp = await client.post("/api/v1/auth/register", json=REGISTER_BODY)
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "alice@example.com"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"
    assert body["memberships"][0]["organization"]["name"] == "Acme Corp"


async def test_register_duplicate_email_conflict(client: AsyncClient):
    assert (await client.post("/api/v1/auth/register", json=REGISTER_BODY)).status_code == 200
    resp = await client.post("/api/v1/auth/register", json=REGISTER_BODY)
    assert resp.status_code == 409


async def test_login_wrong_password_generic_error(client: AsyncClient):
    await client.post("/api/v1/auth/register", json=REGISTER_BODY)
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong-password-123"},
    )
    assert resp.status_code == 401
    # Same message as unknown user — no enumeration.
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password-123"},
    )
    assert unknown.status_code == 401
    assert resp.json()["detail"] == unknown.json()["detail"]


async def test_login_success(client: AsyncClient):
    await client.post("/api/v1/auth/register", json=REGISTER_BODY)
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": REGISTER_BODY["password"]},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_me_requires_token(client: AsyncClient):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer bogus"})
    ).status_code == 401


async def test_short_password_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register", json={**REGISTER_BODY, "password": "short"}
    )
    assert resp.status_code == 422


async def test_capabilities_requires_auth_and_reports_honestly(
    client: AsyncClient, auth_headers: dict
):
    assert (await client.get("/api/v1/meta/capabilities")).status_code == 401
    resp = await client.get("/api/v1/meta/capabilities", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    # Provider details are superadmin-only; even the owner gets llm=null.
    assert body["llm"] is None
    assert body["embeddings"]["provider"] == "local-hash"
    assert body["embeddings"]["semantic"] is False
    assert "pgvector" in body["vector_search"]


async def test_capabilities_llm_superadmin_only(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Only emails in AGENTOS_SUPERADMIN_EMAILS see provider configuration."""
    from app.core.config import get_settings

    # Default: nobody is a superadmin, owner included.
    body = (await client.get("/api/v1/meta/capabilities", headers=auth_headers)).json()
    assert body["llm"] is None
    assert "limits" in body  # non-secret sections stay visible

    monkeypatch.setattr(
        get_settings(), "superadmin_emails", "ops@example.com, Alice@Example.com"
    )
    body = (await client.get("/api/v1/meta/capabilities", headers=auth_headers)).json()
    assert body["llm"] == {
        "provider": "none",
        "configured": False,
        "model": None,
        "note": "No LLM credentials configured — agents run in deterministic mode.",
    }
