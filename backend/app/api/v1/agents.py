"""Agent registry endpoints (read-only in v1)."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import OrgCtx, SessionDep
from app.models import AgentDefinition

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str
    model: str | None
    allowed_tools: list
    max_iterations: int
    timeout_s: int
    enabled: bool
    is_system: bool


@router.get("", response_model=list[AgentOut])
async def list_agents(session: SessionDep, ctx: OrgCtx):
    rows = (
        await session.scalars(
            select(AgentDefinition)
            .where(
                (AgentDefinition.organization_id == ctx.organization.id)
                | (AgentDefinition.organization_id.is_(None))
            )
            .order_by(AgentDefinition.key)
        )
    ).all()
    return [AgentOut.model_validate(a) for a in rows]
