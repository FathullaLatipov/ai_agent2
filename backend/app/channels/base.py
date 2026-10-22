"""Channel adapter abstraction (design for future Telegram/web/etc. interfaces).

Every channel is a thin transport over the SAME service layer: identity is
resolved to an existing AgentOS user, authorization and tenant scoping run in
the services (never in the adapter), and PM commands go through
services.pm.handle_pm_message. No channel gets its own orchestration engine.

The web UI is the first (implicit) adapter. A Telegram adapter is designed but
intentionally NOT implemented in this milestone — see docs/telegram-adapter.md.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChannelIdentity:
    """A channel-local identity resolved to an AgentOS account.

    Linking is explicit and auditable (e.g. a one-time code the user generates
    in the web app and sends to the bot). Unlinked identities get no access.
    """

    channel: str  # "web" | "telegram" | ...
    external_id: str  # e.g. Telegram user id
    user_id: uuid.UUID | None  # linked AgentOS user (None = unlinked)


@dataclass
class ChannelMessage:
    identity: ChannelIdentity
    project_id: uuid.UUID | None  # channel session's selected project
    text: str


@dataclass
class ChannelReply:
    text: str
    requires_action_url: str | None = None  # deep link for approvals etc.


class ChannelAdapter(Protocol):
    """Contract every messaging channel implements."""

    channel_name: str

    async def resolve_identity(self, external_id: str) -> ChannelIdentity:
        """Map a channel identity to a linked AgentOS user (or unlinked)."""
        ...

    async def handle_message(self, message: ChannelMessage) -> ChannelReply:
        """Route a message through the shared PM service. Implementations MUST:
        1) reject unlinked identities, 2) verify project membership via the
        service layer, 3) never execute state changes outside services, and
        4) report only verified backend results."""
        ...

    async def notify(self, user_id: uuid.UUID, text: str) -> None:
        """Push a notification (approval request, task finished) to the user."""
        ...
