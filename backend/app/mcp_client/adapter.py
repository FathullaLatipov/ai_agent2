"""MCP client adapter (stdio transport).

Servers are declared in a JSON config file (AGENTOS_MCP_CONFIG_PATH):

    {"servers": [{"name": "demo", "command": "python", "args": ["scripts/mcp_demo_server.py"],
                  "enabled": true}]}

Discovered tools are registered as `mcp:<server>:<tool>` with requires_approval=True
(external tools are untrusted by default). Tool descriptions from servers are
sanitized before display or prompt use. Secrets are never passed to MCP servers.
"""

import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, create_model

from app.core.config import get_settings
from app.core.logging import get_logger
from app.tools.base import ToolContext, ToolError, ToolSpec
from app.tools.registry import ToolRegistry
from app.tools.registry import registry as global_registry

logger = get_logger("agentos.mcp")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
DISCOVERY_TIMEOUT_S = 20.0
CALL_TIMEOUT_S = 30.0


def sanitize_text(text: str, limit: int = 400) -> str:
    """Strip control characters and cap length — server text is untrusted."""
    return _CONTROL_CHARS.sub("", text or "")[:limit]


def load_server_configs() -> list[dict]:
    path = Path(get_settings().mcp_config_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("mcp config unreadable", error=str(exc))
        return []
    servers = []
    for raw in data.get("servers", []):
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("command"):
            continue
        servers.append(
            {
                "name": str(raw["name"])[:40],
                "command": str(raw["command"]),
                "args": [str(a) for a in raw.get("args", [])],
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return servers


class McpCallOutput(BaseModel):
    content: list[dict] = Field(default_factory=list)
    is_error: bool = False


async def _discover_server_tools(config: dict) -> list[dict]:
    """Connect over stdio and list tools. Returns sanitized tool descriptors."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=config["command"], args=config["args"])
    async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
    tools = []
    for tool in result.tools:
        tools.append(
            {
                "name": sanitize_text(tool.name, 80),
                "description": sanitize_text(tool.description or "", 400),
                "input_schema": tool.input_schema or {"type": "object"},
            }
        )
    return tools


def _make_handler(config: dict, tool_name: str):
    async def handler(_ctx: ToolContext, args: BaseModel) -> McpCallOutput:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=config["command"], args=config["args"])
        try:
            async with asyncio.timeout(CALL_TIMEOUT_S):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            tool_name, arguments=args.model_dump(mode="json")
                        )
        except TimeoutError as exc:
            raise ToolError(f"MCP tool {tool_name} timed out") from exc
        content = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                content.append({"type": "text", "text": sanitize_text(block.text, 4000)})
            else:
                content.append({"type": str(getattr(block, "type", "unknown"))})
        return McpCallOutput(content=content, is_error=bool(result.is_error))

    return handler


def _input_model_from_schema(server: str, tool: str, schema: dict) -> type[BaseModel]:
    """Loose Pydantic model from the server's JSON schema. External schemas are
    untrusted; we accept declared properties (typed as permissive JSON) and let
    the server do fine-grained validation."""
    fields: dict = {}
    for prop in (schema.get("properties") or {}):
        key = re.sub(r"[^a-zA-Z0-9_]", "_", str(prop))[:60]
        required = prop in (schema.get("required") or [])
        fields[key] = (str | int | float | bool | dict | list | None, ... if required else None)
    return create_model(f"Mcp_{server}_{tool}_Input", **fields)


async def discover_and_register(target: ToolRegistry | None = None) -> dict[str, list[str]]:
    """Discover tools from all enabled configured servers; register them.
    Returns {server: [tool names]}. Failures are per-server, logged, never fatal."""
    target = target or global_registry
    registered: dict[str, list[str]] = {}
    for config in load_server_configs():
        if not config["enabled"]:
            continue
        try:
            tools = await _discover_server_tools(config)
        except Exception as exc:
            logger.warning("mcp discovery failed", server=config["name"], error=str(exc))
            registered[config["name"]] = []
            continue
        names = []
        for tool in tools:
            full_name = f"mcp:{config['name']}:{tool['name']}"
            if target.get(full_name) is not None:
                names.append(full_name)
                continue
            target.register(
                ToolSpec(
                    name=full_name,
                    description=f"[MCP:{config['name']}] {tool['description']}",
                    input_model=_input_model_from_schema(
                        config["name"], tool["name"], tool["input_schema"]
                    ),
                    output_model=McpCallOutput,
                    handler=_make_handler(config, tool["name"]),
                    requires_approval=True,  # untrusted by default
                    idempotent=False,
                    timeout_s=CALL_TIMEOUT_S,
                    source=f"mcp:{config['name']}",
                )
            )
            names.append(full_name)
        registered[config["name"]] = names
    return registered


async def describe_servers() -> tuple[list[dict], str]:
    """Status for the Tools page: configured servers and their discovered tools."""
    configs = load_server_configs()
    if not configs:
        return [], (
            "No MCP servers configured. Add entries to the file referenced by "
            "AGENTOS_MCP_CONFIG_PATH (see docs/architecture.md §5)."
        )
    servers = []
    for config in configs:
        entry = {
            "name": config["name"],
            "command": config["command"],
            "enabled": config["enabled"],
            "tools": [],
            "status": "disabled" if not config["enabled"] else "unknown",
        }
        if config["enabled"]:
            try:
                tools = await _discover_server_tools(config)
                entry["tools"] = tools
                entry["status"] = "connected"
            except Exception as exc:
                entry["status"] = f"unreachable: {sanitize_text(str(exc), 120)}"
        servers.append(entry)
    return servers, "Tools from MCP servers require approval before execution."
