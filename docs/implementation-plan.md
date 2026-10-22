# AgentOS — Implementation Plan

> Status: living document. Updated at the end of each milestone.
> Last updated: 2026-10-21 (M5 complete — all milestones done; see §5 for verified results)

## 1. Environment constraints (discovered 2026-10-21)

| Constraint | Finding | Decision |
|---|---|---|
| Python | System has 3.11.9; spec requires 3.12+ | Use `uv` to provision CPython 3.12 for the project venv. Docker image uses `python:3.12-slim`. |
| Docker | Not installed on dev machine | Ship `docker-compose.yml` + Dockerfiles, verified for syntax only. Local verification uses native services. **Compose runtime is unverified on this machine — disclosed in README.** |
| PostgreSQL | 16.15 running natively (localhost:5432, `postgres`/`postgres`) | Use for all dev/integration testing. |
| pgvector | **Not available** in the native Windows Postgres | Dual-path retrieval: pgvector SQL (`<=>` cosine) when extension present (Docker image `pgvector/pgvector:pg16`); otherwise documented brute-force cosine similarity in the service layer. Both are real searches over real embeddings. See ADR-003. |
| Redis | Running natively (localhost:6379) | Use for Taskiq broker + pub/sub event fan-out. |
| LLM credentials | None present | Anthropic provider implemented but **live connectivity unverified**. Deterministic planner/review fallbacks (clearly labeled) keep the full workflow runnable offline. Tests use `FakeLLMProvider`. |
| Node | v22 + npm 10 | Fine for Next.js 15. |

## 2. Architecture decisions (summary — details in architecture.md)

- **ADR-001 Modular monolith**: one FastAPI app + one Taskiq worker process sharing a package (`app/`). No microservices.
- **ADR-002 uv + lockfile**: dependency pinning via `uv.lock`; `pyproject.toml` carries lower bounds.
- **ADR-003 Dual-path vector search**: pgvector when available, service-layer cosine fallback otherwise; capability detected at startup and reported on `/api/v1/meta/capabilities` and in Settings UI.
- **ADR-004 Deterministic-first agents**: every agent has a deterministic code path; LLM augments planning/review/synthesis when a provider is configured. Output always records which mode produced it (`mode: "llm" | "deterministic"`).
- **ADR-005 Events as source of truth**: every state change is persisted as an `execution_events` row, then published on Redis pub/sub. SSE replays from DB (`Last-Event-ID`) then streams live. No fabricated progress.
- **ADR-006 Approval = DB state transition**: approval gates pause the run (LangGraph interrupt + run status `waiting_approval`); resume only via authorized API call that flips the `approval_requests` row. The worker re-enters the graph from the checkpoint.
- **ADR-007 Auth**: JWT access tokens (PyJWT, HS256, 60 min) + bcrypt password hashes. Org-scoped claims. Refresh flow deferred (documented limitation).
- **ADR-008 Tenancy**: every tenant-owned table carries `organization_id`; all service-layer queries filter by it; PostgreSQL RLS policies installed by migration for defense in depth (active when connecting as non-superuser app role — documented).
- **ADR-009 MCP**: official `mcp` Python SDK, stdio transport, servers registered in config; discovered tools are namespaced `mcp:<server>:<tool>`, marked untrusted, require approval by default.

## 3. Milestones

### M1 — Foundation
Repo layout, uv project (Python 3.12), settings, structlog JSON logging, async SQLAlchemy + full schema, Alembic initial migration, JWT auth + org tenancy, health/readiness endpoints, Docker Compose + Dockerfiles, CI workflow, unit+integration tests green.

**Acceptance**: `alembic upgrade head` from clean DB; `pytest` green; `/health` + `/ready` respond; register/login works.

### M2 — Agent engine
Typed tool registry (schemas, permissions, timeouts, approval flags, audit), deterministic tools (dataset inspect, CSV stats/aggregate, calculator, read-only SQL, report render, mock CRM), LLM provider abstraction (Anthropic + Fake + None), LangGraph workflow (planner → router → workers → reviewer → reporter) with Postgres checkpointing, bounded retries, budgets, cancellation, approval interrupts, Taskiq worker execution, SSE event stream.

**Acceptance**: submit task via API without LLM key → plan → tools execute → report persisted; events streamed and replayable; approval gate blocks mock-CRM write until approved via API; tests for registry permissions, state transitions, retries/timeouts.

### M3 — Knowledge & MCP
Document upload (txt/md/csv/pdf), extraction, chunking, embeddings providers (voyage/openai/local-hash — explicit config, no silent fallback), storage, dual-path retrieval with source metadata, tenant access checks, ingestion status tracking, duplicate detection (sha256), MCP client adapter + bundled demo MCP server + discovery API.

**Acceptance**: upload → chunks + embeddings persisted → search returns sources; cross-tenant retrieval blocked (tested); MCP demo server discovered and callable (tested via stdio).

### M4 — Frontend
Next.js 15 + TS + Tailwind v4 + shadcn/ui + TanStack Query. Pages: Overview, Workspace (live run: plan, graph via @xyflow/react, agent timeline, tool calls, result), Runs, Agents, Knowledge, Approvals, Tools (incl. MCP), Settings. SSE with reconnection via Last-Event-ID. Auth screens. Empty/loading/error states everywhere.

**Acceptance**: `npm run build` passes; all pages fetch real API data; live run view driven entirely by persisted events.

### M5 — Production-quality demo
Seed script (demo org/users, sales CSV, policy docs, CRM fixtures), three demo scenarios (A sales, B research, C CRM approval flow), e2e API test (submit → execute → stream → report → reopen), security tests (tenant isolation, authz, approval enforcement), README + security.md + troubleshooting + Mermaid diagram, Makefile, final verification run.

**Acceptance**: documented commands work from clean DB; all three scenarios reproducible; full test suite green; limitations disclosed in README §Limitations.

## 4. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LangGraph API drift between versions | Lock via `uv.lock`; integration tests cover graph compile + run. |
| No LLM key → untestable live path | Provider abstraction + FakeLLM tests; live path marked unverified; opt-in test `-m live_llm`. |
| Windows-native Postgres lacks pgvector & RLS app-role setup differs | Capability detection + fallback path (ADR-003); RLS migration guarded; both covered by tests that skip when unsupported. |
| PDF extraction variance | pypdf text-only; failures mark document `failed` with error, never silent. |
| SSE through proxies | Heartbeat comments every 15s; reconnection with Last-Event-ID replay. |

## 5. Milestone log

Entries are appended here only after the milestone's acceptance checks have actually been run.

- **2026-10-21 — UI/UX redesign (post-M5).** Complete visual overhaul from the dark template to a premium light SaaS design system (tokens: `#F7F8FA`/`#FFFFFF`/`#F1F3F6` surfaces, `#171923`/`#687080` ink, `#E5E7EB` borders, `#635BFF` accent; 8px radius; Geist). Rebuilt: app shell (grouped sidebar Workspace/AI Platform/System, breadcrumb top bar with user menu + pending-approvals chip, mobile slide-over), overview dashboard (4 real metric tiles, recent-runs table, quick-start templates), workspace composer, run view (plan steps with live status, light workflow graph with agent icons, timestamped expandable execution timeline, document-style result panel with copy/download), runs table (+ real "agents involved" via a new aggregate in the list API), approvals, knowledge (drag-and-drop upload), agents/tools as compact expandable lists, settings, login. Fixed along the way: Next 16 "segment dropped from rendering" insight (shell now always renders children during prerender; redirect post-mount via useSyncExternalStore), circular `--font-sans` token (serif fallback bug found via screenshot), missing `remark-gfm` (report tables rendered as plain text), Select components showing raw values, SSE events now carry `created_at`, reporter executive summary no longer embeds tables, research excerpts clip at word boundaries. **Visual QA actually performed**: Playwright script (`frontend/scripts/qa-screenshots.mjs`) captured all pages desktop+mobile as the demo user — zero browser console errors; defects found in screenshots were fixed and re-verified. Gates: frontend lint + build clean; backend `pytest` 62 passed, `ruff` clean.
- **2026-10-21 — M5 complete.** RLS defense-in-depth migration (`c7d8e9f0a1b2`) + provisioning script, with honest activation status in security.md. Idempotent seed (`uv run python -m scripts.seed`): demo org/owner, 4 synthetic documents (90-day sales CSV with two engineered underperformers, travel/refund policies, performance playbook) ingested inline. **Scenario A verified live** against the running API+worker: total 153,907.20 over the dataset-anchored 30-day window; Drift Shelf (7.0%) and Ember Stool (0.7%) flagged; research grounded in the playbook; report with evidence appendix (Scenarios B and C were live-verified in M4's 14-check run). Full migration cycle verified on a scratch DB (upgrade head from clean + downgrade to base). Final gates: backend **62 passed**, ruff clean (incl. scripts), frontend build+lint clean. Docs finalized: README (Mermaid diagram, quick start Windows/Linux, verification status, limitations), docs/demo.md, docs/troubleshooting.md. Known deviations disclosed in README §Limitations.
- **2026-10-21 — M4 complete.** Next.js 16.4 (React 19.3, Tailwind v4, shadcn base-nova, TanStack Query, zustand, @xyflow/react): `npm run build` green (11 routes), `npm run lint` clean. All 8 required pages implemented against the real API with empty/loading/error states; run view renders plan, dependency-layered workflow graph, agent task cards, tool-call table, event feed, report (markdown), approval banners with server-decided actions; SSE hook replays history then streams live with short-lived stream tokens and seq-deduplicated reconnection. **Live stack verification** (real uvicorn + real Taskiq worker over Redis, not the test broker): register → upload → worker ingestion → grounded research run → report citing source → SSE replay over HTTP (16 events, closes on terminal) → CRM run paused `waiting_approval` → approve via API → resumed → completed → outbox delivery recorded. All 14 live checks passed; backend suite re-run green (62). Fixes found by live run: Taskiq broker needs client-mode startup in API lifespan; broker connections need `protocol=2` on old Redis; three Next 16 type/prerender issues.
- **2026-10-21 — M3 complete.** `uv run pytest -q` → **62 passed**, `ruff check` clean. Covered: upload→ingest→search roundtrip (status `ready`, chunks + local-hash embeddings, semantic-fallback retrieval hitting the right excerpt), paragraph-aware chunking with overlap, duplicate upload 409 by sha256, unsupported/empty uploads rejected, broken PDF → status `failed` with error (never fake-ready), CSV → ready dataset without chunks, document tenant isolation (get/list/search/delete), Scenario B end-to-end (research run grounded in uploaded policy with document_chunk evidence cited in the report), MCP stdio discovery against the bundled demo server (namespaced `mcp:demo:*`, requires_approval, sanitized descriptions), MCP call blocked by pending approval then executed after the DB-state approval, server status reporting, control-char sanitization. API drift fixed during milestone: mcp 2.x renamed `FastMCP`→`MCPServer`, `inputSchema`→`input_schema`, `isError`→`is_error`; `documents.embeddings_provider` stores the short provider key (varchar(40)).
- **2026-10-21 — M2 complete.** Verified with `uv run pytest -q` → **48 passed**, `ruff check` clean. Covered: calculator AST safety (6 injection/abuse cases rejected), registry allowlist + schema + timeout enforcement with persisted `tool_calls`, deterministic planner templates (sales/CRM/KB) + plan-structure validation, CSV dataset tools with exact expected numbers and cross-tenant denial, full API-driven runs in deterministic mode (KB question with honest "no evidence", sales scenario computing 30×3012.5 total with underperformer flagged, CRM approve → real outbox write + audit row, CRM reject → run failed), transient-error retry then success, retry-exhaustion → failed → `/retry` → completed, cancellation (pre-execution and waiting-approval), run/approval tenancy isolation, stream-token scoping, SSE replay closing on terminal event. Fixed during milestone: approval decision now commits before re-enqueueing the run (race found by test); `tool_calls.run_id` made nullable (migration `58415d295e81`); session re-fetch after rollback in the retry path.
- **2026-10-21 — M1 complete.** Verified: `uv run alembic upgrade head` applied cleanly to a fresh `agentos` DB (pgvector migration correctly no-ops with notice on the local Windows Postgres); `uv run pytest -q` → **14 passed** (health/readiness, register/login/me, duplicate email 409, generic 401 on bad credentials, token purpose/tamper checks, capabilities endpoint honesty); `uv run ruff check` → clean. Deviation discovered: local Redis is 3.0.504 (old Windows port) — redis-py 8 RESP3 handshake fails; fixed by pinning `protocol=2` (works on Redis 3 and 7). Docker/Compose/CI files authored but **not runtime-verified** (no Docker here).
