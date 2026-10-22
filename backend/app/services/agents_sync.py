"""Sync built-in system agent definitions (code) into the agent_definitions table."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.definitions import SYSTEM_AGENTS
from app.models import AgentDefinition


async def sync_system_agents(session: AsyncSession) -> int:
    """Upsert system agent rows. Returns number of created/updated rows."""
    changed = 0
    for spec in SYSTEM_AGENTS.values():
        row = await session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.key == spec.key,
                AgentDefinition.organization_id.is_(None),
            )
        )
        if row is None:
            session.add(
                AgentDefinition(
                    key=spec.key,
                    name=spec.name,
                    description=spec.description,
                    allowed_tools=list(spec.allowed_tools),
                    max_iterations=spec.max_iterations,
                    timeout_s=spec.timeout_s,
                    is_system=True,
                    enabled=True,
                )
            )
            changed += 1
        else:
            desired = (
                spec.name,
                spec.description,
                list(spec.allowed_tools),
                spec.max_iterations,
                spec.timeout_s,
            )
            current = (
                row.name,
                row.description,
                list(row.allowed_tools),
                row.max_iterations,
                row.timeout_s,
            )
            if desired != current:
                row.name, row.description = spec.name, spec.description
                row.allowed_tools = list(spec.allowed_tools)
                row.max_iterations = spec.max_iterations
                row.timeout_s = spec.timeout_s
                changed += 1
    return changed
