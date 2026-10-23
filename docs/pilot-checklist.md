# AgentOS — Controlled Pilot Execution Checklist

Copy-ready runbook for the Docker host that will run the pilot. Built against
release candidate `317f70f`. Every endpoint, log line, and command below was
verified against the repository; items marked **[host assumption]** must be
confirmed on the target machine — they cannot be verified from the repo.

Shell variable used throughout (keeps every command scoped and non-conflicting):

```bash
CS="docker compose -p agentos-staging -f docker-compose.yml -f docker-compose.staging.yml --env-file .env.staging"
```

---

## 0. Preflight (on the Docker host)

```bash
docker --version && docker compose version     # [host assumption] Engine + Compose v2
git clone <repo> agentos && cd agentos && git checkout 317f70f
cp .env.staging.example .env.staging
```

Edit `.env.staging` **in an editor on the host** (never paste secrets into chat
or shell history):
- `AGENTOS_SECRET_KEY` — replace the whole value with ≥32 random chars. The
  production config guard refuses to boot while `change-me` remains (verified
  behavior, `app/core/config.py::validate_production_config`).
- `POSTGRES_PASSWORD` + the same password inside `AGENTOS_DATABASE_URL`.
- Leave `AGENTOS_LLM_PROVIDER=none` for the first boot (deterministic mode).

Preflight assertions:

```bash
$CS config -q                                   # merged config must validate silently
$CS config | grep -E '^\s+- "' | grep -v 127.0.0.1 && echo "FAIL: non-loopback port" || echo "OK: loopback-only"
$CS config | grep -A3 'postgres:\|redis:' | grep -c 'published' # expect 0 — DB/Redis unpublished
ss -ltn | grep -E ':8800|:3800' && echo "FAIL: port taken" || echo "OK: 8800/3800 free"   # [host assumption]
```

## 1. Start the isolated staging stack

```bash
$CS up -d postgres redis
$CS run --rm api alembic upgrade head           # expect: ... -> b36c90cc92cf, no errors
$CS up -d api worker frontend
```

Verification (endpoints and log lines exist in code — `app/api/v1/health.py`,
`app/worker/tasks.py`):

| Check | Command | Success criterion |
|---|---|---|
| Migrations | `$CS run --rm api alembic current` | `b36c90cc92cf (head)` |
| API liveness | `curl -fsS http://127.0.0.1:8800/api/v1/health` | `{"status":"ok"}` |
| API readiness | `curl -fsS http://127.0.0.1:8800/api/v1/ready` | `postgres: ok`, `redis: ok` |
| Container health | `docker compose -p agentos-staging ps` | api `healthy` (built-in /ready healthcheck) |
| Worker started | `$CS logs worker` | taskiq startup banner; `recovered stale project tasks on startup` appears only if there was something to recover |
| Frontend | open `http://127.0.0.1:3800` | login page renders |
| No secrets in logs | `$CS logs api worker \| grep -iE 'api[_-]?key\|secret\|password'` | only `[REDACTED]`/field names, never values |

Teardown is always `$CS down` (add `-v` only to intentionally delete staging
volumes). Never `docker system prune`, never unscoped `docker rm`.

## 2. Deterministic smoke (no LLM key needed)

Register a workspace in the UI (or seed demo data — development-only
credentials; acceptable on a private, wipeable staging stack):

```bash
$CS run --rm api python -m scripts.seed
```

Then run the three scenarios from `docs/demo.md` (sales analysis, policy
question, CRM follow-up with approval). Success = the criteria printed there,
including the approval pause and resume.

## 3. Live LLM verification (opt-in; currently **NOT RUN** — no key exists)

Provider facts (from code): provider `anthropic`, SDK `anthropic==2.0.0`
(pinned in `backend/uv.lock`), model `AGENTOS_LLM_MODEL` default
`claude-sonnet-5-5`, request timeout `AGENTOS_LLM_TIMEOUT_S=60`, structured
output via forced tool-use validated against `PlanDraft`.

1. On the host, edit `.env.staging`: set `AGENTOS_LLM_PROVIDER=anthropic` and
   paste the key into `AGENTOS_ANTHROPIC_API_KEY=` **in the file only**.
2. Restart app services: `$CS up -d api worker`.
3. Opt-in live test (uses the key from the environment; prints no secrets):
   `$CS run --rm -e AGENTOS_ENV=test api pytest -m live_llm -q`
   — PASS = `1 passed`; without a key it reports `1 skipped` (NOT RUN, never
   claim it passed).
4. Confirm the UI Settings page now shows the provider as configured.

## 4. Bounded live PM end-to-end smoke (only after step 3 passes)

Project goal (local data, non-destructive, bounded by existing budgets —
max 8 plan tasks, 40 LLM calls/run, 60 s/LLM call, task/run timeouts):

> "Analyze the last 30 days of sales, identify underperforming products, and
> review the findings" (requires the seeded demo dataset from step 2).

Success criteria — check each in the UI or via `GET /api/v1/projects/{id}`:

1. **Planning**: plan v1 `proposed`; `created_by_pm` true; plan notes do NOT
   contain "deterministic fallback used" (that phrase means the live call
   failed and the fallback planner ran — then the live smoke did not pass).
2. **Approval gate**: project `awaiting_approval`; `POST /schedule` returns
   409 before approval.
3. **Execution**: after approving, tasks run in dependency order (task
   events' seq ordering), using only registered tools (`tool_calls` rows).
4. **Artifacts & provenance**: each succeeded task's `result.artifacts[*]`
   carries `provenance` (task ordinal, attempt, run_id, agent) and evidence
   refs that resolve (reviewer verifies this deterministically).
5. **Review**: reviewer task `succeeded` with a persisted passing
   `task_reviews` row.
6. **Final status**: project `completed`, consistent with task states; the
   run events include `llm.called` entries with real `input_tokens` /
   `output_tokens` / `request_id` from the provider (never fabricated).

## 5. Backup / restore drill (**NOT RUN** yet; must not touch the live DB)

The restore target is a **separate database** — the drill never overwrites
`agentos`:

```bash
$CS exec postgres pg_dump -U postgres -Fc agentos > agentos-staging.dump
$CS exec postgres createdb -U postgres agentos_restore_check
cat agentos-staging.dump | $CS exec -T postgres pg_restore -U postgres -d agentos_restore_check
$CS exec postgres psql -U postgres -d agentos_restore_check -c "SELECT count(*) FROM agent_runs;"
# compare with the same count on 'agentos'; then clean up the scratch DB only:
$CS exec postgres dropdb -U postgres agentos_restore_check
```

PASS = counts match and the live `agentos` database was never a restore target.
Also back up the uploads volume (document originals).

## 6. Rollback path

`docs/deployment.md` §Application rollback: checkout the previous commit
(`dd52db7` ← `82326c1` ← `4b5a05a`), rebuild, `alembic downgrade` only if the
release added migrations (this RC did not).
