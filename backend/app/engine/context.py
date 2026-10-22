"""AgentContext — the controlled interface agents use to touch the world.

Agents never see raw sessions beyond what tools expose, never call providers
directly, and every tool/LLM call passes budget and permission enforcement here.
"""

import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.engine.errors import BudgetExceededError
from app.events.recorder import EventRecorder
from app.llm.base import LLMProvider, LLMUsage
from app.models import AgentRun, RunTask, ToolCall
from app.tools.base import ToolContext, ToolExecution
from app.tools.registry import ToolRegistry


@dataclass
class AgentContext:
    session: AsyncSession
    settings: Settings
    run: AgentRun
    task: RunTask
    recorder: EventRecorder
    registry: ToolRegistry
    llm: LLMProvider | None
    allowed_tools: list[str]
    prior_outputs: dict[int, dict] = field(default_factory=dict)
    _tool_calls_this_task: int | None = None

    @property
    def deterministic(self) -> bool:
        return self.llm is None

    async def call_tool(self, name: str, arguments: dict) -> ToolExecution:
        if self._tool_calls_this_task is None:
            if self.task.id is None:
                # Transient context (reviewer/reporter stages): no persisted task
                # row — never compare against NULL, which would match every
                # historical task-less tool call in the database.
                self._tool_calls_this_task = 0
            else:
                self._tool_calls_this_task = (
                    await self.session.scalar(
                        select(func.count(ToolCall.id)).where(
                            ToolCall.task_id == self.task.id
                        )
                    )
                ) or 0
        if self._tool_calls_this_task >= self.settings.max_tool_calls_per_task:
            raise BudgetExceededError(
                f"task exceeded the {self.settings.max_tool_calls_per_task} tool-call budget"
            )
        self._tool_calls_this_task += 1
        return await self.registry.execute(
            ToolContext(
                session=self.session,
                organization_id=self.run.organization_id,
                run_id=self.run.id,
                task_id=self.task.id,
                agent_key=self.task.agent_key,
            ),
            tool_name=name,
            arguments=arguments,
            allowed_tools=self.allowed_tools,
            recorder=self.recorder,
        )

    async def llm_structured(
        self, *, system: str, user: str, schema: type[BaseModel], max_tokens: int = 2048
    ) -> BaseModel:
        """LLM call with run-level budget and usage accounting."""
        if self.llm is None:
            raise RuntimeError("llm_structured called in deterministic mode")
        if self.run.llm_calls >= self.settings.max_llm_calls_per_run:
            raise BudgetExceededError(
                f"run exceeded the {self.settings.max_llm_calls_per_run} LLM-call budget"
            )
        result, usage = await self.llm.complete_structured(
            system=system, user=user, schema=schema, max_tokens=max_tokens
        )
        self.run.llm_calls += 1
        _accumulate_usage(self.run, usage)
        await self.recorder.emit(
            self.session,
            run_id=self.run.id,
            organization_id=self.run.organization_id,
            type="llm.called",
            payload={
                "agent": self.task.agent_key,
                "schema": schema.__name__,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                # Opaque provider request id — safe to persist, useful for support.
                "request_id": usage.request_id,
            },
        )
        return result

    def tool_call_ref(self, execution: ToolExecution) -> str:
        return str(execution.tool_call_id)


def _accumulate_usage(run: AgentRun, usage: LLMUsage) -> None:
    if usage.input_tokens is not None:
        run.tokens_input = (run.tokens_input or 0) + usage.input_tokens
    if usage.output_tokens is not None:
        run.tokens_output = (run.tokens_output or 0) + usage.output_tokens
    # estimated_cost_usd stays None unless a pricing table is configured — never invented.


def latest_ready_csv_document_id(prior_params: dict) -> uuid.UUID | None:
    """Helper for planners: explicit param wins; heuristics live in the planner."""
    raw = prior_params.get("dataset_document_id")
    if raw:
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None
    return None
