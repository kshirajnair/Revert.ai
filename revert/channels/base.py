"""Channel interface. A channel only delivers text; it never decides who may be messaged.

That check (only numbers stored against the buyer) and logging live in core/outbox.py,
so every channel gets them for free.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ChannelError(Exception):
    """Delivery failed. The reminder stage stays open and is retried on the next run."""


class Channel(ABC):
    name: str = "base"

    @abstractmethod
    def send_text(self, phone: str, body: str) -> str:
        """Deliver `body` to `phone` (E.164). Returns the provider's message id."""
