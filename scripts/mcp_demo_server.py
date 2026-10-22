"""Demo MCP server (stdio) bundled with AgentOS.

Exposes two harmless tools so MCP discovery and invocation can be demonstrated
and tested without external dependencies. Run via the MCP config file:

    {"servers": [{"name": "demo", "command": "python",
                  "args": ["scripts/mcp_demo_server.py"], "enabled": true}]}
"""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("agentos-demo")


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back (demo tool)."""
    return f"echo: {message}"


@mcp.tool()
def word_count(text: str) -> str:
    """Count words in a text (demo tool)."""
    return f"{len(text.split())} words"


if __name__ == "__main__":
    mcp.run(transport="stdio")
