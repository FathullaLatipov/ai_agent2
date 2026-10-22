# AgentOS — Security Model

## Threat model (summary)

Assets: tenant documents and derived chunks, run outputs/reports, credentials (LLM keys, DB), audit integrity. Adversaries considered: a malicious or curious authenticated user of another org; a prompt-injection payload inside uploaded documents or MCP tool output; a compromised/malicious MCP server; an over-eager LLM output attempting unauthorized actions.

## Controls implemented

### Authentication & sessions
- Password login; bcrypt hashes (cost 12); generic error on failure (no user enumeration).
- JWT access tokens (HS256, `AGENTOS_SECRET_KEY`, 60 min expiry) carrying `sub` (user id) only; org membership and role are **resolved from the database on every request**, never trusted from the token.
- No tokens in query strings except the SSE endpoint, which accepts a short-lived (60 s) single-purpose stream token minted via POST — because `EventSource` cannot send headers.

### Authorization & tenancy
- Role-based checks (`owner/admin/member/viewer`) in the service layer; route dependencies resolve `(user, org, role)` and every query filters by `organization_id`.
- PostgreSQL Row-Level Security policies on tenant tables (migration `c7d8e9f0a1b2`), as **dormant defense in depth**: they bind only when the app connects as a non-owner role (provision with `scripts/provision_rls_role.sql`) that sets `app.current_org` per transaction — wiring that session variable into the connection lifecycle is a deployment-time step, not done by default. Local dev and the default Compose stack connect as the table owner, which PostgreSQL exempts from RLS. Service-layer scoping is always active and is what the isolation tests assert.
- Frontend restrictions are presentation only; every mutation re-checks on the server.

### Agent & tool containment
- Agents can only call tools on their definition's allowlist; the registry enforces this server-side per call.
- Tool arguments validated against Pydantic schemas; unknown fields rejected.
- `sql.read_query`: SELECT-only grammar check + parameterized execution + explicit table/column allowlist + `statement_timeout` + row cap. The LLM never sees a raw session.
- `calculator.evaluate`: AST whitelist (numbers, arithmetic ops, a fixed function set). No `eval`.
- No shell execution tool exists. No arbitrary code execution path.
- High-impact tools (`crm.send_followup`, all MCP tools by default) carry `requires_approval`.

### Approval gates (server-enforced)
A tool flagged `requires_approval` causes: `approval_requests` row (`pending`) → run status `waiting_approval` → LangGraph interrupt persisted. Execution resumes **only** when an authorized human (admin/owner of the same org, and not merely the requester being an agent) flips the row via `POST /approvals/{id}/decide`, which is itself audited. The worker re-checks the row status before executing — approving in the UI without the DB transition is impossible, and replaying the resume without `approved` status is rejected. Expired requests (default 24 h) cannot be approved.

### Prompt injection defenses
- Retrieved document chunks and MCP outputs are injected into prompts inside clearly delimited data blocks with an instruction stating they are untrusted data; they are never concatenated into system prompts.
- The LLM cannot grant permissions: permissions/approvals are DB state read by code; model output is parsed into typed structures, and any tool call it proposes still passes registry permission + approval checks.
- MCP tool descriptions are length-capped and control-character-stripped before being shown to the model; MCP servers never receive secrets or internal tool listings.

### Input & transport hygiene
- Request body size limit (middleware, default 10 MB; uploads 25 MB), upload MIME/extension allowlist, filename sanitization, files stored outside web root under UUID names.
- Rate limiting (Redis fixed-window per user+route class; stricter on auth endpoints).
- CORS restricted to the configured frontend origin.
- Safe errors: internal exceptions → generic 500 + correlation id; details only in server logs.

### Secrets & logging
- Secrets only via environment variables; `.env` is gitignored; `.env.example` has placeholders only.
- Structured logs redact configured tool-argument fields and never log prompts at INFO; prompt/response bodies appear only at DEBUG and are disabled by default.
- No plaintext API keys in any table.

### Auditing
`audit_log` rows for: auth events, run lifecycle, every tool call, approval decisions (who/when/what), document upload/delete, settings changes.

## Residual risks / production hardening TODO

| Gap | Note |
|---|---|
| No refresh tokens / revocation list | 60-min JWTs mitigate; add refresh rotation + denylist for production. |
| HS256 shared secret | Move to RS256/JWKS for multi-service deployments. |
| RLS inactive under local superuser dev | Compose uses `agentos_app` role; never run production as superuser. |
| No MFA / SSO | Out of scope for v1. |
| Rate limiting is fixed-window | Consider sliding window / proxy-level limits in production. |
| MCP stdio servers run as local processes | Only configure trusted binaries; network MCP transports would need TLS + auth review. |
| LLM output may still mislead humans | Reviewer agent + evidence links reduce but do not eliminate; human approval remains the control for actions. |
| Secrets in env of worker processes | Use a secret manager (Vault/SSM) in production. |
