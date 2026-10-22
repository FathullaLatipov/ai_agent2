# AgentOS — Tools, Permissions, and Artifacts (operator guide)

## How tools are registered and assigned to agents

- A tool is a `ToolSpec` (`backend/app/tools/base.py`): stable `name`, description,
  Pydantic **input and output models**, `required_permission`, `requires_approval`,
  `timeout_s`, `idempotent`, and `redact_fields`. Built-ins register on import in
  `app/tools/builtin/__init__.py`; MCP tools are discovered and registered as
  `mcp:<server>:<tool>` with `requires_approval=True`.
- Agents get tools through **allowlists**, the single source of truth being
  `SYSTEM_AGENTS` in `app/agents/definitions.py`, synced to the
  `agent_definitions` table at startup/seed. The registry re-checks the allowlist
  **server-side on every call** — an LLM naming a tool grants nothing.
  A regression test asserts every allowlisted tool is actually registered
  (`tests/test_tool_reliability.py::test_every_agent_allowlisted_tool_is_registered`).
- Capability matching for PM assignment (`app/agents/capabilities.py`) is
  structured metadata (produced/required artifact kinds, assignability), not
  free-text claims; prerequisites are validated against persisted artifacts.

## Invocation lifecycle (what the registry guarantees per call)

1. Unknown tool → rejected before anything runs.
2. Arguments validated against the input schema (unknown/invalid → rejected).
3. Agent allowlist enforced; `requires_approval` tools pause on a pending
   `approval_requests` row — only an org admin/owner can flip it, and the
   worker re-checks the row before executing.
4. `asyncio.timeout(spec.timeout_s)` bounds every call. A timed-out
   **idempotent** tool raises a retryable error (the engine's bounded task
   retry applies); a timed-out **non-idempotent** tool raises a non-retryable
   error — the operation may have executed, so nothing re-fires automatically.
5. Everything persists: `tool_calls` row (status taxonomy: completed / failed /
   rejected / timed_out / pending_approval), redacted arguments, result summary,
   duration, timestamps — plus `audit_log` and `execution_events` entries.
6. Crashing tools surface as the generic "internal tool error"; raw exception
   text never reaches the DB, events, UI, or model context.

**Secrets**: list sensitive argument names in `redact_fields` — they are replaced
with `[REDACTED]` in `tool_calls.arguments` and every event payload while the
tool itself still receives the real value (verified by
`test_redact_fields_sanitize_persisted_args_and_events`). Structured logging
additionally redacts common key names (`app/core/logging.py`).

## Adding a new tool safely (checklist)

1. Define strict Pydantic input/output models (no free-form dicts for inputs).
2. Set an honest `timeout_s`; set `idempotent=False` for anything with side
   effects; set `requires_approval=True` for high-impact operations;
   put secret-bearing argument names into `redact_fields`.
3. Raise `ToolInputError` for bad input, `ToolTransientError` only for failures
   that are safe to retry; never let secrets into exception messages.
4. Register in a module imported by `app/tools/builtin/__init__.py`, then add
   the name to the relevant agent allowlist(s) in `app/agents/definitions.py`.
5. Add a test calling it through `registry.execute` (see
   `tests/test_tools_unit.py` for patterns) — including a denied-allowlist case.

## How artifacts flow between dependent tasks

- A succeeded project task persists `result.artifacts`:
  `{kind, label, data, evidence[], provenance}` where `provenance` records
  `{source, project_task_id, task_ordinal, attempt, run_id, agent}`
  (owner-supplied inputs get `source: "owner-supplied"`).
- When a dependent task is dispatched, the scheduler injects the succeeded
  dependencies' artifacts into the attempt run (`dependency_artifacts` in the
  engine task input, plus pre-completed synthetic engine tasks so agents that
  read prior outputs see them natively). Origin stays attached.
- Acceptance (`app/services/acceptance.py`) decides success from persisted
  state: the run must complete **and** criteria must pass; "non-empty
  deliverable" rejects hollow data (empty strings/lists inside a well-shaped
  object do not count). Failures keep the attempt history and `task_reviews`
  rows; bounded rework attaches explicit feedback.
- Reviewers receive the actual artifacts and deterministically verify cited
  evidence rows (tool calls, documents, chunks) exist **in the same org**.

## Debugging a failed tool invocation

1. Task dialog (Projects UI) → error, attempts, acceptance reviews, run link.
2. Run view → Tool calls tab: per-call status, duration, sanitized error;
   Activity tab → timestamped event timeline (expand rows for payloads).
3. API: `GET /runs/{id}` (`tool_calls[]`), `GET /runs/{id}/events`,
   `GET /projects/{id}/tasks/{task_id}` (`reviews[]`, `result`).
4. DB: `tool_calls` (status/error/idempotency_key), `audit_log`
   (`action='tool.executed'`), `task_reviews` for acceptance verdicts.

## Verification tiers of the test suite

| Tier | What | Where |
|---|---|---|
| Deterministic local (real services, real DB/Redis, real tools) | engine, scheduler, approvals, RAG over local embeddings, kb.search vertical slice, MCP over a local stdio subprocess | most of `tests/` incl. `test_tool_reliability.py`, `test_pm_*`, `test_mcp.py` |
| Mocked (external boundary faked, labeled) | LLM planning/output handling via `FakeLLMProvider` | `test_pm_llm_planning.py`, `test_engine.py` |
| Real external service | **none by default**; opt-in `uv run pytest -m live_llm` requires `AGENTOS_ANTHROPIC_API_KEY` and is skipped otherwise | `test_pm_llm_planning.py::test_live_anthropic_plan_generation` |

## Environment variables (no secret values here — see .env.example)

`AGENTOS_SECRET_KEY` (required), `AGENTOS_DATABASE_URL`, `AGENTOS_REDIS_URL`,
`AGENTOS_LLM_PROVIDER` / `AGENTOS_ANTHROPIC_API_KEY` / `AGENTOS_LLM_MODEL` /
`AGENTOS_LLM_TIMEOUT_S` (live LLM path), `AGENTOS_EMBEDDINGS_PROVIDER`
(+ provider key if semantic), upload/limit knobs (`AGENTOS_MAX_*`,
`AGENTOS_*_TIMEOUT_S`), `AGENTOS_MCP_CONFIG_PATH`.
