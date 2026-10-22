# Telegram Adapter — Design (not implemented)

Status: **design only**, per scope. No bot token, no webhook, no Telegram code
ships in this milestone. The contract lives in `backend/app/channels/base.py`.

## Principles

- Telegram is a *transport*, not a second brain: every command routes through
  `services.pm.handle_pm_message` and the same authorization, tenant scoping,
  state machine, scheduler, and audit logging as the web UI.
- Model/bot output is never proof of execution; replies echo verified backend
  results exactly like the web PM panel.

## Account linking (explicit, auditable)

1. User generates a one-time, short-lived link code in the web app (Settings).
2. User sends `/link <code>` to the bot.
3. Backend verifies the code, stores `channel_identities(channel='telegram',
   external_id=<tg user id>, user_id=…)`, audits the link, and invalidates the code.
4. `/unlink` removes the mapping (audited). Unlinked identities get only the
   linking instructions — no data.

## Command surface (maps 1:1 to existing endpoints/services)

| Telegram | Backend operation |
|---|---|
| `/projects` | list projects for the linked user's org (service-scoped) |
| `/use <project>` | select session project (stored per chat, membership-checked) |
| `/status` | `pm.status_report` |
| free text | `pm.handle_pm_message` (same intent grammar) |
| `/approve <id>` / `/reject <id>` | existing approvals service — **role-checked server-side**; the bot never approves on its own |
| notifications | `ChannelAdapter.notify` on approval.requested / task.failed / project.completed events (subscribed via the existing Redis pub/sub) |

## Security requirements for the future implementation

- Webhook secret-token validation (Telegram `secret_token`) + HTTPS only.
- Callback data signed and bound to (chat, user, approval id) — no guessable ids.
- Approval decisions re-verify role and approval state in the service layer;
  expired/decided approvals answer honestly.
- No secrets, prompts, or tool arguments beyond the redacted summaries already
  shown in the web UI.
- Rate limiting per chat; project context never inferred across chats.

## Why no second engine

`handle_pm_message` already converts natural language to validated backend
operations and reports only persisted results. The adapter adds identity
resolution, chat-session project selection, and notification fan-out — nothing
else. This keeps Scenario-A-style guarantees (prerequisite enforcement, approval
gates) identical across channels by construction.
