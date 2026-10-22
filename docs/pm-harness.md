# AgentOS PM Harness — Architecture & Decisions

> Milestone 1 audit output. Updated as milestones complete. 2026-10-22.

## 1. What exists and is reused (no second engine)

| Existing component | Reused for |
|---|---|
| LangGraph run engine (`app/engine/graph.py`) — plan→select→execute→review→report, budgets, retries, cancellation, approval pauses | **Task execution.** Each project-task attempt runs as a pre-planned, single-task `AgentRun` through this engine unchanged (tools, approvals, events, audit all apply). |
| `AgentRun`/`RunTask`/`ExecutionEvent`/`ToolCall`/`ApprovalRequest` | Per-attempt execution record, linked from `project_tasks.run_id`. |
| Tool registry + approval gates | Unchanged; PM-level tasks inherit them through the run engine. |
| `projects` table (previously unused) | Becomes the PM project entity (new columns: goal, status, settings). |
| `conversations`/`conversation_messages` (previously unused) | PM conversation (new columns: `conversations.project_id`, `conversation_messages.meta`). |
| EventRecorder/SSE pattern | Mirrored for project-level events (`project_events` + `agentos:project-events:<id>` channel). |
| Auth/tenancy/roles, audit log, Taskiq worker | Unchanged; all new ops go through the same service-layer scoping. |

## 2. New control layer (Milestones 2–3)

- **Entities**: `project_plans` (versioned, proposed→approved/rejected/superseded), `project_tasks` (status machine below, acceptance criteria, assignment, attempts, claim fields, `revision_of`), `task_dependencies` (explicit DAG edge table), `task_reviews` (per-attempt acceptance results), `project_events` (append-only, SSE-fed).
- **Task state machine** (service-enforced; illegal transitions raise):
  `draft → planned → (blocked ⇄ ready) → waiting_approval? → assigned → running → reviewing → succeeded | needs_revision | failed`, plus `cancelled`; `needs_revision → ready` (bounded rework with feedback), `failed → ready` (manual retry). Terminal: succeeded, cancelled, and failed-after-limit.
- **DAG validator** (`services/task_graph.py`): Kahn cycle detection, unknown/self reference rejection, readiness = all dependencies `succeeded`; a failed/cancelled dependency keeps dependents blocked with an explicit reason. Runs server-side on every plan proposal and dependency edit.
- **Scheduler** (`services/project_scheduler.py`): recomputes blocked/ready, respects `max_parallel_tasks` per project, and claims atomically — `UPDATE … WHERE status='ready'` (row-count 1 wins) so two workers can never claim the same task. Attempts, claim owner, and claim time are persisted; a recovery sweep re-queues or fails tasks whose worker died (stale claim TTL), idempotently.
- **Capability registry** (`app/agents/capabilities.py`): structured metadata per agent (task kinds, required/produced artifact kinds, assignability, limits) + a matching service. The PM proposes; the backend validates every assignment against this registry — free-form descriptions are never the authority.
- **Acceptance cycle** (`services/acceptance.py`): deterministic criteria evaluation (artifact existence, required output fields, evidence verification) with optional qualitative reviewer pass; failures create bounded rework with persisted feedback and full attempt history.

## 3. PM (Milestones 2 & 4)

- Plan generation runs as a compact LangGraph (`engine/pm_graph.py`): draft (LLM structured output, deterministic template fallback) → server-side validation (DAG + capabilities) → one bounded redraft → persist as a **proposed** plan version. Plans require owner approval before any task executes.
- PM decision policy is enforced in code: eligibility, transitions, permissions, budgets, and claims are deterministic services; the LLM only decomposes, selects, explains, and critiques. The PM cannot mark work done (acceptance is evaluated from persisted state), bypass dependencies (scheduler checks the DAG), or self-approve (approvals API requires an org admin/owner human).
- Explicit agent requests keep three distinct modes: `assign` (record intent), `start_now` (execute only if eligible — otherwise explain blockers and propose prerequisites as a plan revision needing approval), `manage` (PM plans end-to-end). The requested agent is never silently substituted.
- PM conversation: deterministic intent grammar first (plan/assign/start/why-blocked/run-ready/retry/pause/resume/cancel/status/reassign), LLM fallback when configured; every intent maps to a validated backend operation, and the PM reports only verified results.

## 4. Deliberate scope decisions

- A project-task attempt reuses the full run pipeline including its report stage; the per-run report doubles as attempt evidence. Cheap, honest, zero new execution paths.
- `planner`/`reporter` system agents are not directly assignable at project level (the PM owns planning; reporting happens per attempt) — the capability registry marks them so, and the PM explains this instead of failing silently.
- Pause stops new scheduling; already-running attempts finish (cancellation remains available per task/run). Documented in the UI.
- Telegram: adapter interface + plan only (docs/telegram-adapter.md, `app/channels/`); no token, no bot, same service layer for every future channel.

## 5. Milestone log

- **2026-10-22 — M1 complete.** Baseline regression: `uv run pytest -q` → 62 passed before any change. Reuse map and decisions above; acceptance criteria = the eight scenarios in the master prompt (§18).
- **2026-10-22 — M4+M5 complete.** Frontend: Projects section integrated into the existing design system — projects list + create dialog; project page with live status, pause/resume/cancel, **plan-approval banner** (versioned spec with agents, dependencies, assumptions; admin-gated Approve/Reject), **Workflow tab** (interactive dependency DAG derived from `/projects/{id}/graph`, per-status node accents, blocked-reason tooltips, node click → details), **Board tab** (status-grouped columns with blocked reasons and errors), **Timeline tab** (persisted project events), **PM panel** (conversation with intent labels, suggestions, verified-result replies), and a **task details dialog** (criteria, deps/dependents, attempts, artifacts, per-attempt acceptance reviews, run link, assign/start-now/retry/cancel actions). Live project SSE with replay + token-refreshing reconnect. `npm run lint` + `npm run build` clean (16 routes). **Live stack verification** (real uvicorn + Redis worker, not the test broker): Scenario A end-to-end over HTTP (reviewer blocked → prerequisite plan v1 → approve → research then reviewer, project completed) and the full CRM project (PM chat plan → approve → executor draft → send paused `waiting_approval` → human approval → resume → review → completed), all event types verified. **Visual QA performed**: Playwright shots of projects list, completed-project workflow, board, timeline, task dialog, plan-approval state, mobile — zero browser console errors. Real defect found by the live run and fixed: the engine budget counter compared `tool_calls.task_id` against NULL for transient reviewer/reporter contexts, matching all historical task-less calls and eventually failing every run's report stage on a long-lived database. Final gates: backend **93 passed** + ruff clean after the fix; frontend build/lint clean.
- **2026-10-22 — M2+M3 complete (backend).** `uv run pytest -q` → **93 passed** (62 pre-existing regression + 31 new), `ruff` clean. Migration `b36c90cc92cf` (reversible, verified down/up). All eight §18 scenarios pass as automated tests: **A** reviewer-too-early → not executed, prerequisite plan proposed, approved, executed in dependency order (verified via persisted event sequence); **B** supplied verified document → reviewer starts immediately, no forced planning; **C** two independent tasks run before a dependent report (report explicitly blocked first; failed dependency keeps it blocked with an "unrecoverable" reason and no run); **D** unmeetable acceptance criterion → bounded rework with per-attempt persisted reviews and feedback, stops at the retry limit; **E** cyclic plan rejected with a precise error, nothing materialized; **F** conditional-UPDATE claim admits exactly one worker, loser doesn't bump attempts; **G** cross-tenant access 404s everywhere, no state change; **H** stale-claim recovery requeues (or fails at the limit), marks the orphan run failed, and is idempotent. Plus: full CRM project E2E (PM chat → plan → owner approval → execution → tool-level human approval pause → resume → review → completed), PM conversation intents, plan-approval gate blocking the scheduler. Defect found by tests and fixed: dependency outputs now enter attempt runs as marked synthetic pre-completed engine tasks (executor sees prior outputs natively; engine reviewer skips them instead of failing cross-run evidence). Telegram: adapter contract (`app/channels/base.py`) + design doc only, per scope.
