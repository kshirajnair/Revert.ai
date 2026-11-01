"""Channel interface. A channel only delivers; it never decides who may be messaged.

That check (only numbers stored against the buyer), the 24-hour window rule and logging live in
core/outbox.py, so every channel gets them for free.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


class ChannelError(Exception):
    """Delivery failed. The reminder stage stays open and is retried on the next run."""


class OutsideWindow(ChannelError):
    """WhatsApp allows free text only within 24 hours of the dealer's last message.
    Outside it, only pre-approved templates may be sent."""


@dataclass(frozen=True)
class Template:
    """A pre-approved message: fixed `text` with {{1}}, {{2}}... filled from `params`."""

    key: str  # payment_reminder | promise_followup (mapped to the Meta template name by the channel)
    language: str  # en | hi | kn
    text: str
    params: tuple[str, ...]

    def render(self) -> str:
        out = self.text
        for i, p in enumerate(self.params, start=1):
            out = out.replace("{{%d}}" % i, p)
        return out


def template_param(text: str) -> str:
    """WhatsApp rejects template parameters with newlines, tabs or runs of spaces."""
    return re.sub(r"\s+", " ", text).strip()


class Channel(ABC):
    name: str = "base"
    # Free text is allowed only this many hours after the dealer's last message (None: always).
    session_window_hours: int | None = None

    @abstractmethod
    def send_text(self, phone: str, body: str) -> str:
        """Deliver `body` to `phone` (E.164). Returns the provider's message id."""

    def for_phone(self, phone: str) -> Channel:
        """The channel that actually serves `phone` (WhatsApp hands demo numbers to the simulator)."""
        return self

    def send_template(self, phone: str, template: Template) -> str:
        raise ChannelError(f"{self.name} cannot send templates")

    def send_document(self, phone: str, data: bytes, filename: str, caption: str = "") -> str:
        raise ChannelError(f"{self.name} cannot send documents")
