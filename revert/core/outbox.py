"""The single path for messages in and out. Enforces in code:

- Revert only messages phone numbers stored against that buyer (PhoneNotAllowed otherwise).
- WhatsApp's 24-hour rule: free text only within 24 hours of the dealer's last message on that
  number. Outside it a pre-approved template is sent instead, or OutsideWindow is raised.
- Every inbound and outbound message is logged with intent, action and outcome. The logged body
  is what the dealer actually received (the rendered template, when one was used).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from revert.channels.base import Channel, OutsideWindow, Template
from revert.core.models import Buyer, MessageLog
from revert.core.timeutil import utcnow


class PhoneNotAllowed(Exception):
    pass


def _allowed_phone(buyer: Buyer, phone: str | None) -> str:
    phone = phone or buyer.primary_phone
    if not phone or phone not in {p.phone for p in buyer.phones}:
        raise PhoneNotAllowed(f"{phone!r} is not a stored number for {buyer.ledger_name}")
    return phone


def window_open(session, channel: Channel, phone: str, now_utc: datetime) -> bool:
    """True if free text may be sent to `phone` now on this channel."""
    if channel.session_window_hours is None:
        return True
    last_in = session.scalar(
        select(MessageLog.created_at).where(MessageLog.direction == "in", MessageLog.phone == phone,
                                            MessageLog.channel == channel.name)
        .order_by(MessageLog.created_at.desc()).limit(1))
    return last_in is not None and now_utc - last_in < timedelta(hours=channel.session_window_hours)


def send_to_buyer(session, channel: Channel, buyer: Buyer, body: str, *, intent: str, action: str,
                  outcome: str = "sent", meta: dict | None = None, phone: str | None = None,
                  now_utc: datetime | None = None, template: Template | None = None) -> MessageLog:
    """Send free text, or `template` when the 24-hour window is closed. Raises PhoneNotAllowed,
    OutsideWindow (closed window and no template) or ChannelError."""
    phone = _allowed_phone(buyer, phone)
    channel = channel.for_phone(phone)
    now_utc = now_utc or utcnow()
    meta = dict(meta or {})
    if window_open(session, channel, phone, now_utc):
        provider_id = channel.send_text(phone, body)
    elif template is not None:
        provider_id = channel.send_template(phone, template)
        body = template.render()
        meta["template"] = template.key
    else:
        raise OutsideWindow("WhatsApp only allows free text within 24 hours of the dealer's last message")
    return _log_out(session, channel, buyer, phone, body, intent, action, outcome, meta, provider_id, now_utc)


def send_document_to_buyer(session, channel: Channel, buyer: Buyer, data: bytes, filename: str, *,
                           intent: str, action: str, caption: str = "", meta: dict | None = None,
                           phone: str | None = None, now_utc: datetime | None = None) -> MessageLog:
    phone = _allowed_phone(buyer, phone)
    channel = channel.for_phone(phone)
    now_utc = now_utc or utcnow()
    if not window_open(session, channel, phone, now_utc):
        raise OutsideWindow("documents can only be sent within 24 hours of the dealer's last message")
    provider_id = channel.send_document(phone, data, filename, caption)
    body = f"📄 {filename}" + (f"\n{caption}" if caption else "")
    return _log_out(session, channel, buyer, phone, body, intent, action, "sent",
                    {**(meta or {}), "document": filename}, provider_id, now_utc)


def _log_out(session, channel, buyer, phone, body, intent, action, outcome, meta, provider_id, now_utc):
    log = MessageLog(company_id=buyer.company_id, buyer_id=buyer.id, channel=channel.name, direction="out",
                     phone=phone, body=body, intent=intent, action=action, outcome=outcome,
                     meta={**meta, "provider_id": provider_id}, provider_id=provider_id,
                     delivery="sent", created_at=now_utc)
    session.add(log)
    session.flush()
    return log


def log_inbound(session, channel: Channel, *, company_id: int | None, buyer: Buyer | None, phone: str,
                body: str, intent: str = "", action: str = "", outcome: str = "",
                now_utc: datetime | None = None, provider_id: str | None = None,
                meta: dict | None = None) -> MessageLog:
    log = MessageLog(company_id=company_id, buyer_id=buyer.id if buyer else None, channel=channel.name,
                     direction="in", phone=phone, body=body, intent=intent, action=action, outcome=outcome,
                     meta=dict(meta or {}), provider_id=provider_id, created_at=now_utc or utcnow())
    session.add(log)
    session.flush()
    return log
