"""PM harness contracts: plan drafts, intents, API schemas."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.pm import PlanStatus, PMTaskStatus

# ---------- Plan drafting (PM output, validated before persistence) ----------


class AcceptanceCriterion(BaseModel):
    description: str = Field(min_length=3, max_length=500)
    kind: Literal["run_succeeded", "artifact_exists", "output_field", "review_pass"] = (
        "artifact_exists"
    )
    config: dict = Field(default_factory=dict)


class TaskSpec(BaseModel):
    """A task inside a plan draft. `ref` is draft-local; dependencies use refs."""

    ref: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=4000)
    agent: str = Field(min_length=2, max_length=60)
    depends_on: list[str] = Field(default_factory=list, max_length=10)
    params: dict = Field(default_factory=dict)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    expected_deliverables: list[str] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=1000)


class PlanDraft(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    scope: str = ""
    assumptions: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=20)
    notes: str = ""


# ---------- PM conversation ----------

IntentKind = Literal[
    "create_plan",
    "assign_task",
    "start_task",
    "run_ready",
    "explain_blocked",
    "retry_task",
    "reassign_task",
    "pause_project",
    "resume_project",
    "cancel_project",
    "status_report",
    "unknown",
]


class PMIntent(BaseModel):
    kind: IntentKind
    agent_key: str | None = None
    task_ref: str | None = None  # "#3" ordinal or title fragment
    mode: Literal["assign", "start_now", "manage"] | None = None
    raw: str = ""


# ---------- API ----------


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    goal: str = Field(min_length=3, max_length=4000)
    settings: dict = Field(default_factory=dict)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal: str
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    title: str
    description: str
    status: PMTaskStatus
    priority: int
    agent_key: str | None
    requested_agent_key: str | None
    blocked_reason: str | None
    attempts: int
    max_attempts: int
    run_id: uuid.UUID | None
    error: str | None
    revision_feedback: str | None
    acceptance_criteria: list
    expected_deliverables: list
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    depends_on: list[int] = Field(default_factory=list)  # ordinals, filled by API


class TaskDetailOut(TaskOut):
    params: dict = Field(default_factory=dict)
    result: dict | None = None
    dependents: list[int] = Field(default_factory=list)
    reviews: list[dict] = Field(default_factory=list)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: PlanStatus
    goal: str
    scope: str
    assumptions: list
    deliverables: list
    risks: list
    tasks_spec: list
    notes: str
    created_by_pm: bool
    decided_at: dt.datetime | None
    created_at: dt.datetime


class ProjectDetailOut(ProjectOut):
    description: str = ""
    settings: dict = Field(default_factory=dict)
    tasks: list[TaskOut] = Field(default_factory=list)
    plans: list[PlanOut] = Field(default_factory=list)
    task_counts: dict = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=4000)
    agent_key: str | None = None
    depends_on: list[int] = Field(default_factory=list, max_length=10)  # ordinals
    params: dict = Field(default_factory=dict)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    supplied_artifacts: list[dict] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=1000)


class AssignTaskRequest(BaseModel):
    agent_key: str = Field(min_length=2, max_length=60)
    mode: Literal["assign", "start_now", "manage"] = "assign"


class PMMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class PMMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    meta: dict
    created_at: dt.datetime


class ProjectEventOut(BaseModel):
    seq: int
    type: str
    payload: dict
    created_at: dt.datetime


class GraphNodeOut(BaseModel):
    id: str
    ordinal: int
    title: str
    agent_key: str | None
    status: PMTaskStatus
    blocked_reason: str | None


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[dict]  # {source, target} by task id
