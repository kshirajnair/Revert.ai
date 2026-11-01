"""Inbound messages: identify the buyer by phone, log, let the vertical handle it, send the reply."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from revert.channels.base import Channel
from revert.core.models import Buyer, BuyerPhone, MessageLog
from revert.core.outbox import log_inbound, send_to_buyer
from revert.core.phones import normalize_phone
from revert.verticals.collections import handler


@dataclass
class InboundResult:
    buyer: Buyer | None
    inbound: MessageLog
    reply: MessageLog | None


def find_buyer_by_phone(session: Session, phone: str) -> Buyer | None:
    return session.scalar(select(Buyer).join(BuyerPhone).where(BuyerPhone.phone == phone))


def receive_message(session: Session, channel: Channel, phone: str, body: str,
                    now_utc: datetime | None = None, agent=None) -> InboundResult:
    phone = normalize_phone(phone) or phone
    buyer = find_buyer_by_phone(session, phone)
    if buyer is None:
        # Logged, never answered: Revert only talks to numbers stored against a buyer.
        msg = log_inbound(session, channel, company_id=None, buyer=None, phone=phone, body=body,
                          intent="unknown_sender", action="ignored", outcome="no_reply", now_utc=now_utc)
        session.commit()
        return InboundResult(None, msg, None)

    inbound = log_inbound(session, channel, company_id=buyer.company_id, buyer=buyer, phone=phone, body=body,
                          now_utc=now_utc)
    reply = handler.handle(session, buyer, inbound, agent=agent, now_utc=now_utc)
    inbound.intent, inbound.action, inbound.outcome = reply.intent, reply.action, reply.outcome
    inbound.meta = reply.meta
    out = send_to_buyer(session, channel, buyer, reply.body, intent=reply.intent, action="auto_reply",
                        outcome="sent", meta={"in_reply_to": inbound.id}, phone=phone, now_utc=now_utc)
    session.commit()
    return InboundResult(buyer, inbound, out)
