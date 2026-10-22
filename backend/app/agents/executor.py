"""Executor agent — validated business-tool workflows (demo CRM).

Supported actions (from task params):
  prepare_followup: fetch customer + draft a follow-up (reads, no approval)
  send_followup:    send the draft from the dependency task — the registry pauses
                    execution on a pending approval (ApprovalPendingError
                    propagates to the engine).
"""

from app.engine.context import AgentContext
from app.engine.errors import TaskFailedError
from app.schemas.agents import EvidenceRef, ExecutorResult


async def run_executor(ctx: AgentContext) -> ExecutorResult:
    params = ctx.task.input
    action = str(params.get("action") or "")

    if action == "prepare_followup":
        return await _prepare_followup(ctx, params)
    if action == "send_followup":
        return await _send_followup(ctx, params)
    raise TaskFailedError(
        f"executor does not support action '{action}' "
        "(supported: prepare_followup, send_followup)"
    )


async def _prepare_followup(ctx: AgentContext, params: dict) -> ExecutorResult:
    customer_id = str(params.get("customer_id") or "")
    if not customer_id:
        raise TaskFailedError("prepare_followup requires customer_id")
    context = str(params.get("context") or ctx.task.objective)[:500]

    customer = await ctx.call_tool("crm.get_customer", {"customer_id": customer_id})
    draft = await ctx.call_tool(
        "crm.draft_followup", {"customer_id": customer_id, "context": context}
    )
    return ExecutorResult(
        summary=(
            f"Drafted follow-up for {customer.output.name} "  # type: ignore[attr-defined]
            f"({customer.output.company}). Subject: {draft.output.subject}"  # type: ignore[attr-defined]
        ),
        actions=[
            {
                "type": "draft_followup",
                "customer_id": customer_id,
                "subject": draft.output.subject,  # type: ignore[attr-defined]
                "body": draft.output.body,  # type: ignore[attr-defined]
            }
        ],
        evidence=[
            EvidenceRef(
                kind="tool_call", ref_id=str(customer.tool_call_id), label="crm.get_customer"
            ),
            EvidenceRef(
                kind="tool_call", ref_id=str(draft.tool_call_id), label="crm.draft_followup"
            ),
        ],
    )


async def _send_followup(ctx: AgentContext, params: dict) -> ExecutorResult:
    customer_id = str(params.get("customer_id") or "")
    if not customer_id:
        raise TaskFailedError("send_followup requires customer_id")

    # The draft comes from the dependency task's persisted output — not from
    # model-generated state.
    draft_action = None
    for dep_ordinal in ctx.task.depends_on:
        dep_output = ctx.prior_outputs.get(int(dep_ordinal)) or {}
        for action in dep_output.get("actions", []):
            if action.get("type") == "draft_followup" and action.get("customer_id") == customer_id:
                draft_action = action
    if draft_action is None:
        raise TaskFailedError(
            "send_followup found no draft in dependency outputs — run prepare_followup first"
        )

    # Approval gate: this call raises ApprovalPendingError until a human approves.
    sent = await ctx.call_tool(
        "crm.send_followup",
        {
            "customer_id": customer_id,
            "subject": draft_action["subject"],
            "body": draft_action["body"],
        },
    )
    return ExecutorResult(
        summary=(
            f"Follow-up sent to {sent.output.recipient} "  # type: ignore[attr-defined]
            f"(delivery {sent.output.delivery_id}, demo CRM)."  # type: ignore[attr-defined]
        ),
        actions=[
            {
                "type": "sent_followup",
                "customer_id": customer_id,
                "delivery_id": sent.output.delivery_id,  # type: ignore[attr-defined]
                "recipient": sent.output.recipient,  # type: ignore[attr-defined]
            }
        ],
        evidence=[
            EvidenceRef(kind="tool_call", ref_id=str(sent.tool_call_id), label="crm.send_followup")
        ],
    )
