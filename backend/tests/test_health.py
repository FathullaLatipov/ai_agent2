from httpx import AsyncClient


async def test_health(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_reports_dependencies(client: AsyncClient):
    resp = await client.get("/api/v1/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["redis"] == "ok"
