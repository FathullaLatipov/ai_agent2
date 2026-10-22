"""Run/approval API schemas."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ApprovalStatus,
    ExecutionMode,
    RunStatus,
    TaskStatus,
    ToolCallStatus,
)


class CreateRunRequest(BaseModel):
    input_text: str = Field(min_length=3, max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    params: dict = Field(default_factory=dict)
    project_id: uuid.UUID | None = None


class RunTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    agent_key: str
    objective: str
    depends_on: list
    status: TaskStatus
    attempts: int
    output: dict | None
    error: str | None
    mode: ExecutionMode
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    agent_key: str
    arguments: dict
    result_summary: dict | None
    status: ToolCallStatus
    error: str | None
    duration_ms: int | None
    created_at: dt.datetime
    finished_at: dt.datetime | None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    tool_name: str
    operation_summary: str
    payload: dict
    status: ApprovalStatus
    requested_by_agent: str
    decided_by: uuid.UUID | None
    decided_at: dt.datetime | None
    decision_reason: str | None
    expires_at: dt.datetime
    created_at: dt.datetime


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    format: str
    body_markdown: str
    created_at: dt.datetime


class RunSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: RunStatus
    mode: ExecutionMode
    error: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    tokens_input: int | None
    tokens_output: int | None
    llm_calls: int
    # Distinct agent keys of the run's tasks; populated by the list endpoint.
    agents: list[str] = Field(default_factory=list)


class RunDetailOut(RunSummaryOut):
    input_text: str
    plan: dict | None
    budgets: dict
    tasks: list[RunTaskOut] = Field(default_factory=list)
    # Populated by the endpoint after ORM validation:
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)
    report: ReportOut | None = None


class RunListOut(BaseModel):
    items: list[RunSummaryOut]
    total: int


class EventOut(BaseModel):
    seq: int
    type: str
    payload: dict
    created_at: dt.datetime


class DecideApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=2000)


class StreamTokenResponse(BaseModel):
    token: str
    expires_in_s: int
