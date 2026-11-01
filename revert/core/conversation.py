"""Inbound messages: identify the buyer by phone, log, let the vertical handle it, send the reply.

Also applies WhatsApp delivery receipts to the outbound log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from revert.channels.base import Channel, ChannelError
from revert.core import documents
from revert.core.models import Buyer, BuyerPhone, MessageLog, Reminder
from revert.core.outbox import log_inbound, send_document_to_buyer, send_to_buyer
from revert.core.phones import normalize_phone
from revert.core.timeutil import to_ist, today_ist, utcnow
from revert.verticals.collections import handler

log = logging.getLogger(__name__)


@dataclass
class InboundResult:
    buyer: Buyer | None
    inbound: MessageLog
    reply: MessageLog | None
    documents: list[MessageLog] | None = None
    duplicate: bool = False


def find_buyer_by_phone(session: Session, phone: str) -> Buyer | None:
    return session.scalar(select(Buyer).join(BuyerPhone).where(BuyerPhone.phone == phone))


def receive_message(session: Session, channel: Channel, phone: str, body: str,
                    now_utc: datetime | None = None, agent=None, provider_id: str | None = None,
                    media: dict | None = None) -> InboundResult:
    """`media`, for pictures/files/voice notes: {"kind", "caption", "path", "mime"}; `body` is then
    a short description like "[image] caption" for the log."""
    if provider_id:
        seen = session.scalar(select(MessageLog).where(MessageLog.provider_id == provider_id,
                                                       MessageLog.direction == "in"))
        if seen is not None:  # WhatsApp retries webhooks; handle each message once
            return InboundResult(seen.buyer, seen, None, duplicate=True)

    phone = normalize_phone(phone) or phone
    meta = {"media": media} if media else {}
    buyer = find_buyer_by_phone(session, phone)
    if buyer is None:
        # Logged, never answered: Revert only talks to numbers stored against a buyer.
        msg = log_inbound(session, channel, company_id=None, buyer=None, phone=phone, body=body,
                          intent="unknown_sender", action="ignored", outcome="no_reply", now_utc=now_utc,
                          provider_id=provider_id, meta=meta)
        session.commit()
        return InboundResult(None, msg, None)

    inbound = log_inbound(session, channel, company_id=buyer.company_id, buyer=buyer, phone=phone, body=body,
                          now_utc=now_utc, provider_id=provider_id, meta=meta)
    reply = handler.handle(session, buyer, inbound, agent=agent, now_utc=now_utc)
    inbound.intent, inbound.action, inbound.outcome = reply.intent, reply.action, reply.outcome
    inbound.meta = {**meta, **reply.meta}
    out = send_to_buyer(session, channel, buyer, reply.body, intent=reply.intent, action="auto_reply",
                        outcome="sent", meta={"in_reply_to": inbound.id}, phone=phone, now_utc=now_utc)
    session.commit()

    sent_docs = []
    today = to_ist(now_utc).date() if now_utc else today_ist()
    for att in reply.attachments:
        try:
            data, filename = documents.build(session, buyer, att, today)
            sent_docs.append(send_document_to_buyer(
                session, channel, buyer, data, filename, intent=reply.intent, action="send_document",
                meta={"in_reply_to": inbound.id, "attachment": att}, phone=phone, now_utc=now_utc))
            session.commit()
        except (ChannelError, LookupError) as e:  # the text reply already went; the owner can resend
            log.warning("could not send %s to %s: %s", att, buyer.ledger_name, e)
            session.rollback()
    return InboundResult(buyer, inbound, out, sent_docs)


def apply_status(session: Session, provider_id: str, status: str, errors: list[dict] | None = None) -> bool:
    """WhatsApp delivery receipt. Statuses only move forward (sent -> delivered -> read), except
    failed. A failed reminder frees its stages so the next run tries again."""
    msg = session.scalar(select(MessageLog).where(MessageLog.provider_id == provider_id,
                                                  MessageLog.direction == "out"))
    if msg is None:
        return False
    order = {"sent": 1, "delivered": 2, "read": 3}
    if status == "failed":
        msg.delivery, msg.outcome = "failed", "failed"
        msg.meta = {**msg.meta, "errors": errors or [], "failed_at": utcnow().isoformat()}
        session.execute(delete(Reminder).where(Reminder.message_id == msg.id))
    elif status in order and order[status] > order.get(msg.delivery or "", 0) and msg.delivery != "failed":
        msg.delivery = status
    session.commit()
    return True
