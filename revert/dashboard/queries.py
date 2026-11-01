"""Read-side queries for the dashboard. Pure functions over a Session, so tests can call them."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from revert.core.ageing import BuyerAgeing, ageing_report
from revert.core.models import Buyer, Invoice, MessageLog, Receipt, ReceiptAllocation, Reminder
from revert.core.timeutil import to_ist


@dataclass
class OverdueRow:
    ageing: BuyerAgeing
    buyer: Buyer
    last_reminder_at: datetime | None


def overdue_rows(session: Session, company_id: int, as_of: date) -> list[OverdueRow]:
    """Buyers with something overdue, most days late first."""
    rows = [r for r in ageing_report(session, company_id, as_of) if r.total_overdue_paise > 0]
    buyers = {b.id: b for b in session.scalars(
        select(Buyer).where(Buyer.company_id == company_id).options(selectinload(Buyer.phones)))}
    last = dict(session.execute(
        select(Reminder.buyer_id, func.max(Reminder.created_at))
        .where(Reminder.company_id == company_id, Reminder.status == "sent")
        .group_by(Reminder.buyer_id)
    ).all())
    return [OverdueRow(r, buyers[r.buyer_id], last.get(r.buyer_id)) for r in rows]


def needs_owner(session: Session, company_id: int) -> list[MessageLog]:
    """Inbound messages handed to the owner and not yet marked handled."""
    return list(session.scalars(
        select(MessageLog).where(MessageLog.company_id == company_id, MessageLog.direction == "in",
                                 MessageLog.outcome == "awaiting_owner")
        .order_by(MessageLog.created_at.desc())
    ))


def recent_conversations(session: Session, company_id: int, limit: int = 15) -> list[tuple[Buyer, MessageLog]]:
    """Latest message per buyer, newest first."""
    latest = (select(MessageLog.buyer_id, func.max(MessageLog.id).label("mid"))
              .where(MessageLog.company_id == company_id, MessageLog.buyer_id.is_not(None))
              .group_by(MessageLog.buyer_id).subquery())
    rows = session.execute(
        select(Buyer, MessageLog).join(latest, latest.c.buyer_id == Buyer.id)
        .join(MessageLog, MessageLog.id == latest.c.mid)
        .order_by(MessageLog.id.desc()).limit(limit)
    ).all()
    return [(b, m) for b, m in rows]


def thread(session: Session, buyer_id: int, limit: int = 200) -> list[MessageLog]:
    msgs = session.scalars(
        select(MessageLog).where(MessageLog.buyer_id == buyer_id).order_by(MessageLog.id.desc()).limit(limit)
    ).all()
    return list(reversed(msgs))


@dataclass
class MonthTotals:
    collected_paise: int  # all receipts dated this month
    recovered_paise: int  # the part applied to invoices Revert had reminded about before the payment


def month_totals(session: Session, company_id: int, as_of: date) -> MonthTotals:
    start = as_of.replace(day=1)
    collected = session.scalar(
        select(func.coalesce(func.sum(Receipt.amount_paise), 0))
        .where(Receipt.company_id == company_id, Receipt.date >= start, Receipt.date <= as_of)
    )
    reminded_on: dict[str, date] = {}
    for number, sent_at in session.execute(
        select(Invoice.number, func.min(Reminder.created_at)).join(Reminder, Reminder.invoice_id == Invoice.id)
        .where(Reminder.company_id == company_id, Reminder.status == "sent").group_by(Invoice.number)
    ):
        reminded_on[number] = to_ist(sent_at).date()
    recovered = 0
    for alloc, rdate in session.execute(
        select(ReceiptAllocation, Receipt.date).join(Receipt)
        .where(Receipt.company_id == company_id, Receipt.date >= start, Receipt.date <= as_of)
    ):
        first = reminded_on.get(alloc.invoice_number)
        if first is not None and first <= rdate:
            recovered += alloc.amount_paise
    return MonthTotals(collected, recovered)
