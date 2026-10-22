"""Report rendering tool — persists the final report as a durable artifact."""

import uuid

from pydantic import BaseModel

from app.models import Report
from app.schemas.agents import FinalReport
from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry


class RenderReportInput(FinalReport):
    """The validated FinalReport contract is the tool input."""


class RenderReportOutput(BaseModel):
    report_id: uuid.UUID
    title: str
    markdown: str


async def _render_report(ctx: ToolContext, args: RenderReportInput) -> RenderReportOutput:
    if ctx.run_id is None:
        raise ToolInputError("report.render requires a run context")
    markdown = args.to_markdown()
    report = Report(
        organization_id=ctx.organization_id,
        run_id=ctx.run_id,
        title=args.title,
        format="markdown",
        body_markdown=markdown,
        artifacts={"evidence_count": len(args.evidence_appendix)},
    )
    ctx.session.add(report)
    await ctx.session.flush()
    return RenderReportOutput(report_id=report.id, title=args.title, markdown=markdown)


registry.register(
    ToolSpec(
        name="report.render",
        description="Render and persist the final report (markdown) for the current run.",
        input_model=RenderReportInput,
        output_model=RenderReportOutput,
        handler=_render_report,
        timeout_s=15.0,
    )
)
