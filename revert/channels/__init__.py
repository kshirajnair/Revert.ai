"""Messaging channels: web_simulator (Phase 2), whatsapp (Phase 4)."""
from __future__ import annotations

from revert.channels.base import Channel


def get_channel(name: str = "web_simulator", settings=None) -> Channel:
    if name == "web_simulator":
        from revert.channels.web_simulator import WebSimulatorChannel

        return WebSimulatorChannel()
    if name == "whatsapp":
        from revert.channels.whatsapp import WhatsAppChannel
        from revert.config import get_settings

        s = settings or get_settings()
        return WhatsAppChannel(
            s.whatsapp_token, s.whatsapp_phone_number_id, s.whatsapp_api_version,
            template_names={"payment_reminder": s.whatsapp_reminder_template,
                            "promise_followup": s.whatsapp_followup_template},
        )
    raise ValueError(f"Unknown CHANNEL={name!r} (use web_simulator or whatsapp)")
