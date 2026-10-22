"""Planner — natural language request → validated PlannerOutput.

LLM mode: structured planning prompt, schema-validated, structure-checked.
Deterministic mode: rule-based templates for the recognized task families
(CSV/sales analysis, knowledge-base question, CRM follow-up), labeled as such.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.engine.errors import PlanInvalidError
from app.llm.base import LLMProvider
from app.models import Document, DocumentStatus
from app.schemas.agents import PlannerOutput, PlanTask

PLANNER_SYSTEM = """You are the planning component of AgentOS, a multi-agent platform.
Decompose the user's request into the smallest set of tasks that achieves it.

Available worker agents:
- research: searches the organization's uploaded documents / knowledge base. Use for
  questions about documents, policies, context, causes.
- analyst: computes metrics over uploaded CSV datasets with deterministic tools
  (inspect, stats, aggregate). Use for any numeric/dataset work. Params it
  understands: dataset_document_id, group_by, value_column, date_column, last_days.
- executor: calls business tools (demo CRM: list/get customer, draft follow-up,
  send follow-up). Sending requires human approval. Params: customer_id, action
  ("prepare_followup" or "send_followup"), context.

Rules:
- Create only tasks that are necessary. One task per distinct unit of work.
- Ordinals start at 0 with no gaps; depends_on may reference earlier ordinals only.
- Do NOT add review or reporting tasks — the platform runs those stages itself.
- Put concrete parameters in params. Reference available resources exactly as given."""


def _context_block(documents: list[dict], request_params: dict) -> str:
    lines = ["Available resources:"]
    if documents:
        lines.append("Uploaded documents (id | filename | type | status):")
        lines.extend(
            f"- {d['id']} | {d['filename']} | {d['content_type']} | {d['status']}"
            for d in documents
        )
    else:
        lines.append("No uploaded documents.")
    if request_params:
        lines.append(f"Caller-supplied params: {request_params}")
    lines.append("Demo CRM customers exist (use crm.list_customers via executor if needed).")
    return "\n".join(lines)


async def _org_documents(session: AsyncSession, organization_id: uuid.UUID) -> list[dict]:
    docs = (
        await session.scalars(
            select(Document)
            .where(Document.organization_id == organization_id)
            .order_by(Document.created_at.desc())
            .limit(25)
        )
    ).all()
    return [
        {
            "id": str(d.id),
            "filename": d.filename,
            "content_type": d.content_type,
            "status": d.status.value if isinstance(d.status, DocumentStatus) else str(d.status),
        }
        for d in docs
    ]


_CSV_HINT = re.compile(r"\b(sales|revenue|csv|dataset|product|metric|analy[sz]e)\b", re.I)
_CRM_HINT = re.compile(r"\b(customer|crm|follow[- ]?up|outreach)\b|CUST-\d+", re.I)
_CUSTOMER_ID = re.compile(r"\bCUST-\d{3,6}\b")


def deterministic_plan(
    input_text: str, documents: list[dict], params: dict, settings: Settings
) -> PlannerOutput:
    """Rule-based planning for recognized task families."""
    ready_csvs = [
        d
        for d in documents
        if d["status"] == "ready"
        and (d["content_type"] == "text/csv" or d["filename"].lower().endswith(".csv"))
    ]
    dataset_id = str(params.get("dataset_document_id") or "") or (
        ready_csvs[0]["id"] if ready_csvs else ""
    )
    customer_match = _CUSTOMER_ID.search(input_text)
    customer_id = str(params.get("customer_id") or "") or (
        customer_match.group(0) if customer_match else ""
    )

    tasks: list[PlanTask] = []
    if _CRM_HINT.search(input_text) and customer_id:
        tasks = [
            PlanTask(
                ordinal=0,
                agent="executor",
                objective=f"Retrieve customer {customer_id} and draft a follow-up",
                params={
                    "action": "prepare_followup",
                    "customer_id": customer_id,
                    "context": input_text[:500],
                },
            ),
            PlanTask(
                ordinal=1,
                agent="executor",
                objective=f"Send the approved follow-up to {customer_id}",
                depends_on=[0],
                params={"action": "send_followup", "customer_id": customer_id},
            ),
        ]
        goal = f"Prepare and send a follow-up to CRM customer {customer_id}"
    elif _CSV_HINT.search(input_text) and dataset_id:
        tasks = [
            PlanTask(
                ordinal=0,
                agent="analyst",
                objective="Compute revenue and product metrics from the dataset",
                params={
                    "dataset_document_id": dataset_id,
                    "last_days": int(params.get("last_days") or 30),
                },
            ),
            PlanTask(
                ordinal=1,
                agent="research",
                objective=(
                    "Search the knowledge base for context on underperforming products "
                    "and possible causes"
                ),
                depends_on=[0],
                params={"query": "product performance causes pricing promotion inventory"},
            ),
        ]
        goal = "Analyze the dataset and identify underperforming products with context"
    else:
        tasks = [
            PlanTask(
                ordinal=0,
                agent="research",
                objective=f"Search the knowledge base to answer: {input_text[:300]}",
                params={"query": input_text[:300]},
            )
        ]
        goal = f"Answer from the knowledge base: {input_text[:200]}"

    plan = PlannerOutput(
        goal=goal,
        tasks=tasks,
        notes="Plan produced by the deterministic rule-based planner (no LLM configured).",
    )
    plan.validate_structure(settings.max_plan_tasks)
    return plan


async def create_plan(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    input_text: str,
    params: dict,
    llm: LLMProvider | None,
    settings: Settings,
) -> PlannerOutput:
    documents = await _org_documents(session, organization_id)
    if llm is None:
        return deterministic_plan(input_text, documents, params, settings)

    user_prompt = (
        f"User request:\n{input_text}\n\n{_context_block(documents, params)}\n\n"
        f"Produce at most {settings.max_plan_tasks} tasks."
    )
    plan, _usage = await llm.complete_structured(
        system=PLANNER_SYSTEM, user=user_prompt, schema=PlannerOutput, max_tokens=2048
    )
    try:
        plan.validate_structure(settings.max_plan_tasks)
    except ValueError as exc:
        raise PlanInvalidError(f"LLM plan failed validation: {exc}") from exc

    # Harden: planner must not reference documents that don't exist.
    known_ids = {d["id"] for d in documents}
    for task in plan.tasks:
        ref = task.params.get("dataset_document_id")
        if ref and str(ref) not in known_ids:
            raise PlanInvalidError(f"plan references unknown document {ref}")
    return plan
