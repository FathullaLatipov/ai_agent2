"""Mock CRM — a safe stand-in for an external business API (demo data only).

Customer records come from a seeded JSON fixture (scripts/data/crm_customers.json,
clearly labeled demo data). Reads are free; `crm.send_followup` is a high-impact
write and requires human approval (enforced by the registry, not the UI). "Sent"
messages land in var/crm_outbox.jsonl so the side effect is inspectable.
"""

import datetime as dt
import json
import uuid
from pathlib import Path

from pydantic import BaseModel, EmailStr, Field

from app.core.config import get_settings
from app.tools.base import ToolContext, ToolInputError, ToolSpec
from app.tools.registry import registry

_FIXTURE = Path(__file__).resolve().parents[4] / "scripts" / "data" / "crm_customers.json"


def _load_customers() -> dict[str, dict]:
    if not _FIXTURE.is_file():
        raise ToolInputError("mock CRM fixture missing (scripts/data/crm_customers.json)")
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return {c["customer_id"]: c for c in data["customers"]}


class GetCustomerInput(BaseModel):
    customer_id: str = Field(min_length=1, max_length=40)


class CustomerOut(BaseModel):
    customer_id: str
    name: str
    email: EmailStr
    company: str
    segment: str
    account_value_usd: float
    last_contact: str
    open_issues: list[str]
    notes: str
    demo_data: bool = True


async def _get_customer(_ctx: ToolContext, args: GetCustomerInput) -> CustomerOut:
    customer = _load_customers().get(args.customer_id)
    if customer is None:
        raise ToolInputError(f"customer '{args.customer_id}' not found in the demo CRM")
    return CustomerOut(**customer)


class ListCustomersInput(BaseModel):
    segment: str | None = None


class CustomerSummary(BaseModel):
    customer_id: str
    name: str
    company: str
    segment: str


class ListCustomersOutput(BaseModel):
    customers: list[CustomerSummary]
    demo_data: bool = True


async def _list_customers(_ctx: ToolContext, args: ListCustomersInput) -> ListCustomersOutput:
    customers = _load_customers().values()
    if args.segment:
        customers = [c for c in customers if c["segment"].lower() == args.segment.lower()]
    return ListCustomersOutput(
        customers=[CustomerSummary(**c) for c in customers]
    )


class DraftFollowupInput(BaseModel):
    customer_id: str
    context: str = Field(min_length=3, max_length=2000)


class DraftFollowupOutput(BaseModel):
    customer_id: str
    subject: str
    body: str
    demo_data: bool = True


async def _draft_followup(ctx: ToolContext, args: DraftFollowupInput) -> DraftFollowupOutput:
    customer = _load_customers().get(args.customer_id)
    if customer is None:
        raise ToolInputError(f"customer '{args.customer_id}' not found in the demo CRM")
    issues = customer.get("open_issues", [])
    issue_lines = (
        "".join(f"- {issue}\n" for issue in issues)
        if issues
        else "- (no open issues on record)\n"
    )
    body = (
        f"Hi {customer['name'].split()[0]},\n\n"
        f"Following up regarding: {args.context}\n\n"
        f"Our records show these open items for {customer['company']}:\n"
        f"{issue_lines}\n"
        f"Last contact on record: {customer['last_contact']}.\n\n"
        "Would a short call this week work to close these out?\n\n"
        "Best regards,\nAccount Team"
    )
    return DraftFollowupOutput(
        customer_id=args.customer_id,
        subject=f"Follow-up: {customer['company']} — {args.context[:60]}",
        body=body,
    )


class SendFollowupInput(BaseModel):
    customer_id: str
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=5000)


class SendFollowupOutput(BaseModel):
    delivery_id: str
    customer_id: str
    recipient: str
    sent_at: str
    demo_data: bool = True


async def _send_followup(ctx: ToolContext, args: SendFollowupInput) -> SendFollowupOutput:
    customer = _load_customers().get(args.customer_id)
    if customer is None:
        raise ToolInputError(f"customer '{args.customer_id}' not found in the demo CRM")
    settings = get_settings()
    outbox = Path(settings.upload_dir).parent / "crm_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    delivery_id = f"demo-{uuid.uuid4().hex[:12]}"
    sent_at = dt.datetime.now(dt.UTC).isoformat()
    record = {
        "delivery_id": delivery_id,
        "organization_id": str(ctx.organization_id),
        "run_id": str(ctx.run_id) if ctx.run_id else None,
        "customer_id": args.customer_id,
        "recipient": customer["email"],
        "subject": args.subject,
        "body": args.body,
        "sent_at": sent_at,
        "demo_data": True,
    }
    with outbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return SendFollowupOutput(
        delivery_id=delivery_id,
        customer_id=args.customer_id,
        recipient=customer["email"],
        sent_at=sent_at,
    )


registry.register(
    ToolSpec(
        name="crm.list_customers",
        description="List customers in the demo CRM, optionally filtered by segment.",
        input_model=ListCustomersInput,
        output_model=ListCustomersOutput,
        handler=_list_customers,
        timeout_s=10.0,
    )
)
registry.register(
    ToolSpec(
        name="crm.get_customer",
        description="Fetch a customer record from the demo CRM.",
        input_model=GetCustomerInput,
        output_model=CustomerOut,
        handler=_get_customer,
        timeout_s=10.0,
    )
)
registry.register(
    ToolSpec(
        name="crm.draft_followup",
        description="Draft a follow-up message for a demo CRM customer (no send).",
        input_model=DraftFollowupInput,
        output_model=DraftFollowupOutput,
        handler=_draft_followup,
        timeout_s=10.0,
    )
)
registry.register(
    ToolSpec(
        name="crm.send_followup",
        description="Send a follow-up message via the demo CRM. High-impact: requires approval.",
        input_model=SendFollowupInput,
        output_model=SendFollowupOutput,
        handler=_send_followup,
        requires_approval=True,
        idempotent=False,  # never auto-retried
        timeout_s=15.0,
    )
)
