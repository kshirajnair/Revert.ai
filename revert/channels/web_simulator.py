"""Web chat simulator channel.

Nothing leaves the laptop: an outbound message is "delivered" by being written to
message_log, and the simulator page renders the dealer's phone from that log.
Inbound messages come from the simulator page via core.conversation.receive_message.
"""
from __future__ import annotations

import uuid

from revert.channels.base import Channel


class WebSimulatorChannel(Channel):
    name = "web_simulator"

    def send_text(self, phone: str, body: str) -> str:
        return f"sim-{uuid.uuid4().hex[:12]}"
