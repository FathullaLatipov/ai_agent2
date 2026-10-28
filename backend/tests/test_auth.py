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
    assert body["llm"]["configured"] is False
    assert body["embeddings"]["provider"] == "local-hash"
    assert body["embeddings"]["semantic"] is False
    assert "pgvector" in body["vector_search"]


async def test_capabilities_llm_hidden_from_non_admins(
    client: AsyncClient, auth_headers: dict, db_session
):
    """Provider configuration is admin-only; members get llm=null."""
    import uuid as uuid_mod

    from app.core.security import create_access_token, hash_password
    from app.models import MemberRole, OrganizationMember, User

    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    org_id = uuid_mod.UUID(me.json()["memberships"][0]["organization"]["id"])

    member = User(
        email="bob@example.com",
        password_hash=hash_password("another-strong-pass"),
        full_name="Bob Member",
    )
    db_session.add(member)
    await db_session.flush()
    db_session.add(
        OrganizationMember(
            organization_id=org_id, user_id=member.id, role=MemberRole.MEMBER
        )
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/meta/capabilities",
        headers={"Authorization": f"Bearer {create_access_token(member.id)}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm"] is None
    # Non-secret sections remain visible.
    assert body["embeddings"]["provider"] == "local-hash"
    assert "limits" in body

    owner = (await client.get("/api/v1/meta/capabilities", headers=auth_headers)).json()
    assert owner["llm"] is not None
