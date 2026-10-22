"""Typed contracts for agent inputs/outputs. Validated at every node boundary."""

from typing import Literal

from pydantic import BaseModel, Field

AgentKey = Literal["planner", "research", "analyst", "executor", "reviewer", "reporter"]

# Agent keys the planner may assign work to.
WORKER_AGENT_KEYS = ("research", "analyst", "executor")


class PlanTask(BaseModel):
    ordinal: int = Field(ge=0, le=50)
    agent: Literal["research", "analyst", "executor"]
    objective: str = Field(min_length=3, max_length=2000)
    depends_on: list[int] = Field(default_factory=list, max_length=10)
    params: dict = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """A validated execution plan."""

    goal: str = Field(min_length=3, max_length=2000)
    tasks: list[PlanTask] = Field(min_length=1)
    notes: str = ""

    def validate_structure(self, max_tasks: int) -> None:
        """Structural checks beyond field validation. Raises ValueError."""
        if len(self.tasks) > max_tasks:
            raise ValueError(f"plan has {len(self.tasks)} tasks; budget allows {max_tasks}")
        ordinals = [t.ordinal for t in self.tasks]
        if sorted(ordinals) != list(range(len(self.tasks))):
            raise ValueError("task ordinals must be 0..n-1 without gaps")
        seen: set[int] = set()
        for task in sorted(self.tasks, key=lambda t: t.ordinal):
            for dep in task.depends_on:
                if dep not in seen:
                    raise ValueError(
                        f"task {task.ordinal} depends on {dep}, which is not an earlier task"
                    )
            seen.add(task.ordinal)


class EvidenceRef(BaseModel):
    """A pointer to real, persisted evidence — never invented."""

    kind: Literal["document_chunk", "tool_call", "dataset"]
    ref_id: str
    label: str = ""
    excerpt: str = ""


class ResearchFindings(BaseModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class MetricValue(BaseModel):
    name: str
    value: float | int | str
    unit: str = ""
    computed_by: str = ""  # tool call id — metrics always come from tools


class AnalysisResult(BaseModel):
    summary: str
    metrics: list[MetricValue] = Field(default_factory=list)
    tables: list[dict] = Field(default_factory=list)
    method: str = ""
    limitations: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ExecutorResult(BaseModel):
    summary: str
    actions: list[dict] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    severity: Literal["blocker", "warning"]
    description: str
    task_ordinal: int | None = None


class ReviewVerdict(BaseModel):
    approved: bool
    issues: list[ReviewIssue] = Field(default_factory=list)
    revision_request: str | None = None
    revision_task_ordinal: int | None = None
    checks_performed: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    title: str
    executive_summary: str
    methodology: str
    findings: str
    recommendations: str
    limitations: str
    evidence_appendix: list[EvidenceRef] = Field(default_factory=list)

    def to_markdown(self) -> str:
        parts = [
            f"# {self.title}",
            "## Executive summary",
            self.executive_summary,
            "## Methodology",
            self.methodology,
            "## Findings",
            self.findings,
            "## Recommendations",
            self.recommendations,
            "## Limitations",
            self.limitations,
        ]
        if self.evidence_appendix:
            parts.append("## Evidence")
            for ev in self.evidence_appendix:
                label = ev.label or ev.ref_id
                excerpt = f" — {ev.excerpt}" if ev.excerpt else ""
                parts.append(f"- **{label}** ({ev.kind} `{ev.ref_id}`){excerpt}")
        return "\n\n".join(parts)
