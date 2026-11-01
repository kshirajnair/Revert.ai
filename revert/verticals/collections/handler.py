"""Inbound message handler for the collections vertical.

Phase 2: every message is handed to the owner with a polite acknowledgement.
Phase 3 replaces `handle` with the AI agent (intents, tools, promises, disputes).
"""
from __future__ import annotations

from dataclasses import dataclass

from revert.core.models import Buyer, MessageLog
from revert.verticals.collections.templates import handoff_reply


@dataclass(frozen=True)
class Reply:
    body: str
    intent: str  # what the buyer wanted
    action: str  # what Revert did about it
    outcome: str  # state of the inbound message afterwards


def handle(session, buyer: Buyer, inbound: MessageLog) -> Reply:
    return Reply(
        body=handoff_reply(company_name=buyer.company.name, buyer_name=buyer.ledger_name),
        intent="unclassified",
        action="handoff_owner",
        outcome="awaiting_owner",
    )
