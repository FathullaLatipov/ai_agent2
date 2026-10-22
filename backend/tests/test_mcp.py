"""MCP adapter: stdio discovery and gated execution against the bundled demo server.

Marked integration: spawns a real subprocess (the demo MCP server) over stdio.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from app.mcp_client import adapter
from app.models import ApprovalRequest, ApprovalStatus
from app.tools.base import ApprovalPendingError, ToolContext
from app.tools.registry import ToolRegistry
from tests.helpers import make_org

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_SERVER = REPO_ROOT / "scripts" / "mcp_demo_server.py"

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp_config(tmp_path, monkeypatch):
    config_path = tmp_path / "mcp.servers.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "demo",
                        "command": sys.executable,
                        "args": [str(DEMO_SERVER)],
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mcp_config_path", str(config_path))
    return config_path


async def test_discovery_registers_namespaced_untrusted_tools(mcp_config):
    local = ToolRegistry()
    registered = await adapter.discover_and_register(local)
    assert "demo" in registered
    assert "mcp:demo:echo" in registered["demo"]
    spec = local.get("mcp:demo:echo")
    assert spec is not None
    assert spec.requires_approval is True  # untrusted by default
    assert spec.idempotent is False
    assert spec.source == "mcp:demo"
    assert spec.description.startswith("[MCP:demo]")


async def test_mcp_tool_gated_then_executes_after_approval(mcp_config, db_session):
    from app.events.recorder import EventRecorder
    from app.services.runs import create_run

    local = ToolRegistry()
    await adapter.discover_and_register(local)

    org, user = await make_org(db_session)
    run = await create_run(
        db_session,
        EventRecorder(),
        organization_id=org.id,
        user_id=user.id,
        input_text="mcp demo",
    )
    ctx = ToolContext(
        session=db_session,
        organization_id=org.id,
        run_id=run.id,
        task_id=None,
        agent_key="executor",
    )
    arguments = {"message": "hello agentos"}

    # First call pauses on approval — the gate applies to MCP tools.
    with pytest.raises(ApprovalPendingError) as exc_info:
        await local.execute(
            ctx, tool_name="mcp:demo:echo", arguments=arguments, allowed_tools=None
        )
    approval = await db_session.get(ApprovalRequest, exc_info.value.approval_request_id)
    assert approval.status == ApprovalStatus.PENDING

    # A human approves (state transition), then the call goes through.
    approval.status = ApprovalStatus.APPROVED
    approval.decided_at = dt.datetime.now(dt.UTC)
    await db_session.flush()

    result = await local.execute(
        ctx, tool_name="mcp:demo:echo", arguments=arguments, allowed_tools=None
    )
    texts = [c.get("text", "") for c in result.output.content]
    assert any("echo: hello agentos" in t for t in texts)


async def test_describe_servers_reports_status(mcp_config):
    servers, note = await adapter.describe_servers()
    assert servers[0]["name"] == "demo"
    assert servers[0]["status"] == "connected"
    tool_names = {t["name"] for t in servers[0]["tools"]}
    assert {"echo", "word_count"} <= tool_names
    assert "approval" in note


def test_sanitize_strips_control_chars():
    assert adapter.sanitize_text("hi\x00\x1b[31mthere\x07", 50) == "hi[31mthere"


def test_missing_config_yields_empty_list(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mcp_config_path", str(tmp_path / "missing.json"))
    assert adapter.load_server_configs() == []
