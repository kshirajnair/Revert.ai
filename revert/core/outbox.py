"""The single path for messages in and out. Enforces two hard rules in code:

- Revert only messages phone numbers stored against that buyer (PhoneNotAllowed otherwise).
- Every inbound and outbound message is logged with intent, action and outcome.
"""
from __future__ import annotations

from datetime import datetime

from revert.channels.base import Channel
from revert.core.models import Buyer, MessageLog
from revert.core.timeutil import utcnow


class PhoneNotAllowed(Exception):
    pass


def send_to_buyer(session, channel: Channel, buyer: Buyer, body: str, *, intent: str, action: str,
                  outcome: str = "sent", meta: dict | None = None, phone: str | None = None,
                  now_utc: datetime | None = None) -> MessageLog:
    phone = phone or buyer.primary_phone
    allowed = {p.phone for p in buyer.phones}
    if not phone or phone not in allowed:
        raise PhoneNotAllowed(f"{phone!r} is not a stored number for {buyer.ledger_name}")
    provider_id = channel.send_text(phone, body)
    log = MessageLog(company_id=buyer.company_id, buyer_id=buyer.id, channel=channel.name, direction="out",
                     phone=phone, body=body, intent=intent, action=action, outcome=outcome,
                     meta={**(meta or {}), "provider_id": provider_id}, created_at=now_utc or utcnow())
    session.add(log)
    session.flush()
    return log


def log_inbound(session, channel: Channel, *, company_id: int | None, buyer: Buyer | None, phone: str,
                body: str, intent: str = "", action: str = "", outcome: str = "",
                now_utc: datetime | None = None) -> MessageLog:
    log = MessageLog(company_id=company_id, buyer_id=buyer.id if buyer else None, channel=channel.name,
                     direction="in", phone=phone, body=body, intent=intent, action=action, outcome=outcome,
                     meta={}, created_at=now_utc or utcnow())
    session.add(log)
    session.flush()
    return log
