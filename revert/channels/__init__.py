"""Messaging channels: web_simulator (Phase 2), whatsapp_cloud (Phase 4)."""
from __future__ import annotations

from revert.channels.base import Channel


def get_channel(name: str = "web_simulator") -> Channel:
    if name == "web_simulator":
        from revert.channels.web_simulator import WebSimulatorChannel

        return WebSimulatorChannel()
    if name == "whatsapp":
        raise NotImplementedError("WhatsApp Cloud API channel arrives in Phase 4")
    raise ValueError(f"Unknown CHANNEL={name!r}")
