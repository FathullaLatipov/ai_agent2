# AgentOS — Troubleshooting

## Backend

**`/ready` reports redis unavailable / `unknown command 'HELLO'`**
Old Redis servers (< 6, e.g. the Windows 3.0.x port) don't speak RESP3. AgentOS pins
`protocol=2` everywhere; if you see HELLO errors you are running modified code or a
different client — keep `protocol=2` in `app/core/redis.py` and `app/worker/broker.py`.

**`SendTaskError: Cannot send task to the queue`**
The API enqueues through the Taskiq broker, which must be started in client mode —
this happens in the FastAPI lifespan. If you embed the app elsewhere, call
`await broker.startup()` before serving.

**Runs stay `queued` forever**
The worker isn't running (Redis itself is fine — otherwise submission would have
failed, see below). Start it with
`uv run taskiq worker app.worker.broker:broker --workers 1` from `backend/` and watch
its log for the task pickup line.

**Submission returns 503 "task queue unavailable"**
Redis was unreachable at submit time. Nothing is silently orphaned: the run is
recorded as `failed` with the queue error (retry it via the Runs page or
`POST /runs/{id}/retry` once Redis is back); project tasks stay `READY` with
their claims released; uploads are marked `failed` with guidance; approval
decisions remain recorded, and re-running a gated task creates a **fresh**
approval request. Fix Redis, then retry/schedule.

**`alembic upgrade head` prints "pgvector extension not available"**
Expected on PostgreSQL installs without pgvector (default Windows installer). The
migration is a documented no-op; retrieval uses the service-layer cosine fallback and
the Settings page shows which path is active. Use the `pgvector/pgvector:pg16` image
(Compose/CI) for the SQL path.

**Login rejects an email that was seeded directly into the DB**
`EmailStr` validation rejects reserved TLDs (`.local`, `.test`). Use a normal domain.

**Tests fail with connection errors**
Tests need the `agentos_test` database plus Redis. They truncate `agentos_test`
between tests and run the real migrations each session — never point
`AGENTOS_TEST_DATABASE_URL` at a database you care about.

**Document stuck `pending`** — worker not running (see above).
**Document `failed`** — open the Knowledge page; the error is stored on the row
(e.g. PDF with no extractable text). Fix the file and re-upload; identical bytes are
rejected as duplicates per organization.

## Frontend

**Every API call fails with a CORS error**
The API only allows `AGENTOS_FRONTEND_ORIGIN` (default `http://localhost:3000`). If
Next picked another port (it prints `Port 3000 is in use … using 3002`), set
`AGENTOS_FRONTEND_ORIGIN=http://localhost:3002` in `.env` and restart the API.

**Live view says "reconnecting…" forever**
SSE stream tokens are 60-second single-purpose tokens; the hook re-mints on every
reconnect. Persistent failure usually means the API restarted with a new
`AGENTOS_SECRET_KEY` — sign out/in — or a proxy is buffering SSE (disable buffering
for `/api/v1/runs/*/events/stream`).

**Signed out unexpectedly** — access tokens live 60 minutes and there is no refresh
flow in v1 (documented limitation). Sign in again.

## MCP

**Server shows `unreachable` on the Tools page**
The command in `mcp.servers.json` must start a stdio MCP server. Test the bundled one
directly: `uv run python ../scripts/mcp_demo_server.py` from `backend/` should block
silently (it's waiting on stdio) — Ctrl+C to exit. Paths are resolved relative to the
API's working directory.

**MCP tool call never executes**
By design: MCP tools are untrusted and require approval. Check the Approvals page.

## Windows specifics

- Always let `uv` provision Python (`uv sync` in `backend/`); the system 3.11 is too old.
- The event-loop policy is set to Selector in all entrypoints (asyncpg requirement
  under some configurations); if you add a new entrypoint script, copy that stanza.
- `make` usually isn't available — run the commands from the Makefile directly.
