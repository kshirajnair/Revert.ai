"""Promise-to-pay tracking.

A promise installment is *kept* once receipts dated on or after the day it was made add up to its
cumulative amount ("50k now, rest on 15th": the second part is kept when the total reaches the
whole balance). It is *broken* when its date has passed without that. A broken promise gets one
follow-up message (outside quiet hours, never to a paused dealer); after that the normal reminder
schedule takes over again.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revert.agent import guard, tools
from revert.agent.tools import Ctx, day, money
from revert.channels.base import Channel, ChannelError
from revert.core.models import Buyer, Company, PaymentPromise, Receipt
from revert.core.outbox import PhoneNotAllowed, send_to_buyer
from revert.core.reminders import get_policy, in_quiet_hours
from revert.core.timeutil import IST
from revert.verticals.collections.replies import Composer

log = logging.getLogger(__name__)

FOLLOWUP_ACTION = "promise_followup"


def received_since(session: Session, buyer_id: int, since: date) -> int:
    return session.scalar(select(func.coalesce(func.sum(Receipt.amount_paise), 0))
                          .where(Receipt.buyer_id == buyer_id, Receipt.date >= since))


def evaluate_promises(session: Session, company_id: int, as_of: date) -> dict[str, int]:
    """Mark open promises kept or broken. Returns counts."""
    counts = {"kept": 0, "broken": 0}
    for p in session.scalars(select(PaymentPromise).where(PaymentPromise.company_id == company_id,
                                                          PaymentPromise.status == "open")):
        if received_since(session, p.buyer_id, p.promised_on) >= p.cumulative_paise:
            p.status = "kept"
            counts["kept"] += 1
        elif p.due_date < as_of:
            p.status = "broken"
            counts["broken"] += 1
    session.commit()
    return counts


def promise_followups(session: Session, channel: Channel, now: datetime | None = None) -> list[str]:
    """Evaluate promises, then follow up once on each broken one. Returns buyer names followed up."""
    now = (now or datetime.now(IST)).astimezone(IST)
    now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    done = []
    for company in session.scalars(select(Company)).all():
        evaluate_promises(session, company.id, now.date())
        if in_quiet_hours(now, get_policy(session, company.id)):
            continue
        pending = session.scalars(select(PaymentPromise).where(
            PaymentPromise.company_id == company.id, PaymentPromise.status == "broken",
            PaymentPromise.followed_up_at.is_(None)).order_by(PaymentPromise.due_date)).all()
        seen: set[int] = set()
        for p in pending:
            if p.buyer_id in seen:  # one follow-up per buyer per run; mark the rest as covered
                p.followed_up_at = now_utc
                continue
            seen.add(p.buyer_id)
            buyer = session.get(Buyer, p.buyer_id)
            if buyer.paused:
                p.followed_up_at = now_utc  # the dealer asked us to stop; the owner sees the broken promise
                continue
            body, problems = followup_message(session, company, buyer, p, now.date())
            if problems:
                log.error("guard blocked promise follow-up to %s: %s", buyer.ledger_name, problems)
                continue
            try:
                send_to_buyer(session, channel, buyer, body, intent="promise_followup", action=FOLLOWUP_ACTION,
                              meta={"promise_id": p.id}, now_utc=now_utc)
            except (ChannelError, PhoneNotAllowed) as e:
                log.warning("promise follow-up to %s failed: %s", buyer.ledger_name, e)
                continue
            p.followed_up_at = now_utc
            done.append(buyer.ledger_name)
        session.commit()
    return done


def followup_message(session: Session, company: Company, buyer: Buyer, p: PaymentPromise,
                     today: date) -> tuple[str, list[str]]:
    ctx = Ctx(session, company, buyer, today)
    ctx.facts.add(money(p.amount_paise), day(p.due_date))
    c = Composer(buyer.language, company.name, buyer.ledger_name)
    c.say("promise_followup", amount=money(p.amount_paise), date=day(p.due_date))
    dues = tools.open_dues(ctx)
    if dues.total_paise:
        c.say("still_pending", total=money(dues.total_paise))
    c.synced(tools.synced_at(ctx))
    body = c.text()
    return body, guard.check_reply(body, ctx.facts.texts, "", company.name, c.lang)
