# AgentOS — Deployment & Operations Guide (controlled pilot)

Scope: the recommended way to run AgentOS for a controlled pilot on a single
host or small VM, reusing the existing stack (FastAPI + Taskiq worker +
PostgreSQL 16 + Redis + Next.js). No Kubernetes — nothing here needs it.

## Topology

```
[Next.js frontend]  →  [FastAPI api :8000]  →  PostgreSQL 16 (pgvector image)
                            ↕ enqueue/pubsub        ↑
                       [Taskiq worker ×1..N]  ──────┘
                            ↕
                          Redis 7
```

- `docker-compose.yml` ships this exact topology (`pgvector/pgvector:pg16`,
  `redis:7-alpine`, `api`, `worker`, `frontend`). **Honest status**: Compose was
  authored on a machine without Docker and is not runtime-verified — treat the
  first `docker compose up` in a staging copy as a required validation step.
- Scale by adding `worker` replicas; task claiming is a conditional UPDATE, so
  concurrent workers are safe by design (tested). Keep `api` single-instance
  for the pilot (SSE + in-process rate-limit windows are per-instance).

## Configuration & startup validation

All config via `AGENTOS_*` environment variables (`.env.example` is the
catalog; never commit a real `.env`). With `AGENTOS_ENV=production` the API
**refuses to boot** on an example/short `AGENTOS_SECRET_KEY` and logs warnings
for localhost CORS origins and default `postgres/postgres` credentials
(`app/core/config.py::validate_production_config`).

Production checklist:
- strong `AGENTOS_SECRET_KEY` (≥32 random chars), dedicated DB user (consider
  `scripts/provision_rls_role.sql` to activate the RLS layer),
  real `AGENTOS_FRONTEND_ORIGIN`, TLS terminated by your reverse proxy
  (disable buffering for `/api/v1/**/events/stream`).
- Do **not** run `scripts.seed` in production — it creates the demo account
  (`demo@example.com`, documented dev-only) and synthetic demo documents.
- LLM path only if intended: `AGENTOS_LLM_PROVIDER=anthropic` +
  `AGENTOS_ANTHROPIC_API_KEY`; otherwise leave `none` (deterministic mode).

## Migration sequence (and rollback)

```
alembic upgrade head        # before starting new app code
# rollback: alembic downgrade -1   (all revisions have tested downgrades)
```
Order for upgrades: stop worker → migrate → start new api+worker. The worker
runs a stale-claim recovery sweep at startup, so interrupted attempts are
re-queued or failed honestly, never silently completed.

## Health checks & supervision

- API: `GET /api/v1/health` (liveness), `GET /api/v1/ready` (checks
  PostgreSQL and Redis; 503 when degraded).
- Worker: no HTTP endpoint (taskiq); supervise the process (systemd/compose
  `restart: always`) and alert on the absence of task pickup logs. A crashed
  worker is recovered by the startup sweep + claim TTL (15 min default).
- Redis loss: event publishing degrades gracefully (SSE replays from
  PostgreSQL on reconnect). Submission paths fail **honestly**: runs are marked
  failed with a 503 and are retryable, scheduler claims are released so tasks
  stay READY, approval decisions are preserved (recovery demands fresh
  authorization for gated tools), and uploads are marked failed with guidance.
  PostgreSQL loss: hard down, by design.

## Backups & recovery

- PostgreSQL is the source of truth (runs, tasks, events, documents metadata,
  approvals, audit). Nightly `pg_dump -Fc agentos` + WAL archiving if you need
  point-in-time recovery; test restores into a scratch database.
- Also back up the upload directory (`AGENTOS_UPLOAD_DIR`) — document originals
  live there; chunks/embeddings are re-derivable via re-ingestion.
- Redis needs no backup (queue + pub/sub only; no durable state).

## Resource limits & cost controls (enforced in code)

Per run: `AGENTOS_MAX_LLM_CALLS_PER_RUN`, `AGENTOS_RUN_TIMEOUT_S`,
`AGENTOS_TASK_TIMEOUT_S`, per-task tool-call budget, bounded retries.
Per project: `max_parallel_tasks` (settings), plan size
(`AGENTOS_MAX_PLAN_TASKS`), bounded rework (`max_attempts`). Per user: API
rate limits (stricter on auth). Known gap for multi-tenant SaaS use: no
cross-run per-organization spending budget — acceptable for a controlled
pilot with trusted users, track for production.

## Dev mode vs production mode

| | development | production |
|---|---|---|
| config guard | off | fail-fast on weak secret; warnings logged |
| logs | console renderer | JSON (structlog) |
| rate limiting | on (off only under `test`) | on |
| seed/demo data | optional, labeled | never run |
| RLS layer | dormant (superuser) | activate via app role (see security.md) |
