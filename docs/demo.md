# AgentOS — Demo Walkthrough

Prerequisites: stack running and seeded (see README Quick start). Sign in as
`demo@example.com` / `agentos-demo-123`. All seeded content is synthetic and labeled
demo data. Everything below works **without an LLM API key** (deterministic mode —
the run header shows which mode executed).

## Scenario A — Sales intelligence

1. Open **Workspace** and submit:
   > Analyze the last 30 days of sales, identify underperforming products, and research possible causes
2. Watch the live view: the planner creates two tasks (analyst → research); the
   workflow graph animates the running stage; the tool timeline fills with
   `dataset.inspect`, `dataset.stats`, `dataset.aggregate`, `kb.search`.
3. Expected result (deterministic, from the seeded `sales_demo.csv`):
   - Total revenue **153,907.20** over the dataset-anchored 30-day window
     (2026-09-22 → 2026-10-21).
   - **Drift Shelf** (7.0% share) and **Ember Stool** (0.7% share) flagged as
     underperforming — criterion: share below 50% of the mean share, stated in the
     method section.
   - Research findings grounded in `product_performance_playbook.md` (pricing drift,
     promotion coverage, inventory, catalog quality, lifecycle).
4. Open **Result** and download the report (`Report` button). Every metric lists the
   tool call that computed it; the evidence appendix references real tool calls and
   document chunks.

## Scenario B — Research assistant

1. Submit:
   > What does our travel policy say about business class flights?
2. Expected: the research agent retrieves from `company_travel_policy.md`; the answer
   states business class needs flights > 6 hours + VP approval, with chunk-level
   source references. The Knowledge page's search box shows the same retrieval with
   its method (`semantic-fallback` with local-hash embeddings, or `semantic-pgvector`
   under Docker).
3. Negative case: ask about something absent (e.g. "What is our parental leave
   policy?") — the run completes with an explicit *missing information* statement and
   the report lists it under Limitations. No invented answer.

## Scenario C — Business workflow with human approval

1. Submit:
   > Prepare a customer follow-up for CUST-1001 about the open renewal and send it
2. Task 0 completes (customer fetched from the mock CRM, follow-up drafted). Task 1
   attempts `crm.send_followup` → the run pauses in **waiting_approval**; an amber
   banner shows the exact message to be sent.
3. Open **Approvals** (badge in the sidebar): review the operation arguments, add a
   reason, **Approve**. The run resumes automatically and completes; the task output
   records the delivery id.
4. Verify the side effect and the trail:
   - the "sent" message is in `backend/var/crm_outbox.jsonl` (or the configured
     var dir) — labeled `demo_data: true`;
   - the approval row records who decided, when, and why (also in `audit_log`);
   - rejecting instead fails the run with the rejection recorded.
5. Note the enforcement model: the Approve button only flips a database row through
   an authorized, audited endpoint — the worker re-checks that row before executing.
   A viewer/member account sees the banner but gets no decision buttons, and the API
   rejects their decision regardless of the UI.

## Things worth showing off along the way

- Kill the browser mid-run and reopen the run from **Runs** — the full timeline
  replays from persisted events (nothing lives only in the browser).
- **Runs → Retry** on a failed run resumes from the failed task, not from scratch.
- **Tools** lists every registered tool with its schemas and approval flags, plus
  live MCP server discovery when `mcp.servers.json` is configured (copy
  `mcp.servers.example.json`; the bundled demo server needs no external services).
- **Settings** reports provider/capability status honestly — including that
  local-hash embeddings are lexical, and which vector-search path is active.
