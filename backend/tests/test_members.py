"""Team management: invites, role changes, removal, hierarchy enforcement."""

from httpx import AsyncClient

from tests.conftest import REGISTER_BODY


async def _invite(client: AsyncClient, headers: dict, email: str, role: str) -> dict:
    resp = await client.post(
        "/api/v1/org/invites", json={"email": email, "role": role}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _accept(client: AsyncClient, token: str, name: str = "New Member") -> dict:
    resp = await client.post(
        f"/api/v1/invites/{token}/accept",
        json={"full_name": name, "password": "a-strong-password"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_invite_flow_and_role_visibility(client: AsyncClient, auth_headers: dict):
    invite = await _invite(client, auth_headers, "worker@example.com", "member")
    assert invite["token"]

    # Public inspection works without auth and shows org + role.
    pub = await client.get(f"/api/v1/invites/{invite['token']}")
    assert pub.status_code == 200
    assert pub.json() == {
        "organization_name": "Acme Corp",
        "email": "worker@example.com",
        "role": "member",
        "valid": True,
        "reason": None,
    }

    member_headers = await _accept(client, invite["token"])

    # The new member joined the same org with the invited role.
    me = (await client.get("/api/v1/auth/me", headers=member_headers)).json()
    assert me["memberships"][0]["role"] == "member"
    assert me["memberships"][0]["organization"]["name"] == "Acme Corp"

    # Members do not see provider details and cannot manage the team.
    caps = (
        await client.get("/api/v1/meta/capabilities", headers=member_headers)
    ).json()
    assert caps["llm"] is None
    assert (
        await client.get("/api/v1/org/members", headers=member_headers)
    ).status_code == 403

    # The invite is single-use.
    again = await client.post(
        f"/api/v1/invites/{invite['token']}/accept",
        json={"full_name": "X", "password": "a-strong-password"},
    )
    assert again.status_code == 400

    # Owner sees both members in the list.
    members = (await client.get("/api/v1/org/members", headers=auth_headers)).json()
    assert {m["email"]: m["role"] for m in members} == {
        "alice@example.com": "owner",
        "worker@example.com": "member",
    }


async def test_role_change_and_removal(client: AsyncClient, auth_headers: dict):
    invite = await _invite(client, auth_headers, "worker@example.com", "viewer")
    member_headers = await _accept(client, invite["token"])
    members = (await client.get("/api/v1/org/members", headers=auth_headers)).json()
    target = next(m for m in members if m["email"] == "worker@example.com")
    owner = next(m for m in members if m["email"] == "alice@example.com")

    # Promote viewer -> admin.
    resp = await client.patch(
        f"/api/v1/org/members/{target['id']}", json={"role": "admin"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    # An admin cannot touch the owner or grant admin.
    assert (
        await client.patch(
            f"/api/v1/org/members/{owner['id']}",
            json={"role": "member"},
            headers=member_headers,
        )
    ).status_code == 400
    assert (
        await client.delete(
            f"/api/v1/org/members/{owner['id']}", headers=member_headers
        )
    ).status_code == 400
    assert (
        await client.post(
            "/api/v1/org/invites",
            json={"email": "x@example.com", "role": "admin"},
            headers=member_headers,
        )
    ).status_code == 400

    # Owner removes the member; their only workspace -> account deactivated.
    resp = await client.delete(
        f"/api/v1/org/members/{target['id']}", headers=auth_headers
    )
    assert resp.status_code == 204
    denied = await client.get("/api/v1/auth/me", headers=member_headers)
    assert denied.status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "worker@example.com", "password": "a-strong-password"},
    )
    assert login.status_code == 401


async def test_invite_guards(client: AsyncClient, auth_headers: dict):
    # Cannot invite an existing member's email.
    resp = await client.post(
        "/api/v1/org/invites",
        json={"email": REGISTER_BODY["email"], "role": "member"},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    # Owner role is not grantable (schema-level).
    resp = await client.post(
        "/api/v1/org/invites",
        json={"email": "x@example.com", "role": "owner"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # Re-inviting supersedes the previous link.
    first = await _invite(client, auth_headers, "twice@example.com", "member")
    second = await _invite(client, auth_headers, "twice@example.com", "member")
    old = await client.get(f"/api/v1/invites/{first['token']}")
    assert old.json()["valid"] is False
    assert (await client.get(f"/api/v1/invites/{second['token']}")).json()["valid"] is True

    # Revocation invalidates the link; unknown tokens 404.
    assert (
        await client.delete(f"/api/v1/org/invites/{second['id']}", headers=auth_headers)
    ).status_code == 204
    assert (await client.get(f"/api/v1/invites/{second['token']}")).json()["valid"] is False
    assert (await client.get("/api/v1/invites/not-a-real-token")).status_code == 404

    # Team endpoints require auth.
    assert (await client.get("/api/v1/org/members")).status_code == 401
