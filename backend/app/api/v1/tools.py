"""Tool registry endpoints: registered tools, schemas, permissions, MCP status."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import OrgCtx

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolOut(BaseModel):
    name: str
    description: str
    source: str
    requires_approval: bool
    required_permission: str
    timeout_s: float
    idempotent: bool
    input_schema: dict
    output_schema: dict


@router.get("", response_model=list[ToolOut])
async def list_tools(_ctx: OrgCtx):
    import app.tools.builtin  # noqa: F401 — ensure registration
    from app.tools.registry import registry

    return [
        ToolOut(
            name=spec.name,
            description=spec.description,
            source=spec.source,
            requires_approval=spec.requires_approval,
            required_permission=spec.required_permission,
            timeout_s=spec.timeout_s,
            idempotent=spec.idempotent,
            input_schema=spec.input_model.model_json_schema(),
            output_schema=spec.output_model.model_json_schema(),
        )
        for spec in registry.list_specs()
    ]


class McpStatusOut(BaseModel):
    servers: list[dict]
    note: str


@router.get("/mcp", response_model=McpStatusOut)
async def mcp_status(_ctx: OrgCtx):
    from app.mcp_client.adapter import describe_servers

    servers, note = await describe_servers()
    return McpStatusOut(servers=servers, note=note)
