"""Reminder engine.

Each open invoice moves through stages, in days relative to its due date (owner-editable,
default -3, 0, +7, +15, +30). On every run:

1. For each open invoice, the *current* stage is the latest one already reached. If that stage
   has no Reminder row yet, it is due. Earlier stages that were missed (first run, or deferred
   past the next stage) are not sent separately, so a buyer never gets a backlog burst.
2. Due invoices are grouped per buyer into ONE message.
3. Nothing goes out during quiet hours (default 20:00-09:00 IST), to paused buyers, or to a
   buyer who already got `max_per_week` reminders in the last 7 days. These are deferred, not
   dropped: no row is written, so the next run tries again.
4. A sent message writes one `sent` Reminder row per invoice, so the stage never repeats.

Agent holds (Phase 3): invoices under dispute are left out; a buyer with a dispute not tied to an
invoice is skipped; a buyer with an open promise not yet due, or an unchecked "I already paid"
from the last few days, is deferred.

Stop on payment: after each sync, `on_payments_synced` marks the current stage of every invoice
that received money as `cancelled`, so nothing is sent for it. A fully paid invoice has no
balance and is never picked up again. A part-paid one resumes at its next stage.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from revert.agent.tools import holds_for
from revert.channels.base import Channel, ChannelError
from revert.core.models import Buyer, Company, Invoice, MessageLog, Reminder, ReminderPolicy
from revert.core.outbox import PhoneNotAllowed, send_to_buyer
from revert.core.timeutil import IST, format_ist
from revert.verticals.collections.templates import ReminderLine, reminder_message

log = logging.getLogger(__name__)

REMINDER_ACTION = "reminder_sent"


@dataclass
class BuyerResult:
    buyer: str
    invoices: list[str]
    status: str  # sent | deferred | skipped | failed
    reason: str = ""


@dataclass
class RunReport:
    at: datetime  # IST
    results: list[BuyerResult] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)  # buyers sent a broken-promise follow-up

    def by_status(self, status: str) -> list[BuyerResult]:
        return [r for r in self.results if r.status == status]

    @property
    def sent(self) -> list[BuyerResult]:
        return self.by_status("sent")

    def summary(self) -> str:
        counts = {s: len(self.by_status(s)) for s in ("sent", "deferred", "skipped", "failed")}
        return ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "nothing due"

    def reasons(self, max_names: int = 3) -> str:
        """Why buyers were not sent to, one clause per reason: names when few, a count when many."""
        grouped: dict[str, list[str]] = defaultdict(list)
        for r in self.results:
            if r.status != "sent":
                grouped[r.reason].append(r.buyer)
        parts = []
        for reason, names in grouped.items():
            who = ", ".join(names) if len(names) <= max_names else f"{len(names)} dealers"
            parts.append(f"{reason[:1].upper()}{reason[1:]}: {who}")
        return "; ".join(parts)


def get_policy(session: Session, company_id: int) -> ReminderPolicy:
    policy = session.scalar(select(ReminderPolicy).where(ReminderPolicy.company_id == company_id))
    if policy is None:
        policy = ReminderPolicy(company_id=company_id)
        session.add(policy)
        session.flush()
    return policy


def in_quiet_hours(now: datetime, policy: ReminderPolicy) -> bool:
    """`now` in IST. Quiet window is [start, end), wrapping past midnight when start > end."""
    h, start, end = now.astimezone(IST).hour, policy.quiet_start_hour, policy.quiet_end_hour
    if start == end:
        return False
    return (h >= start or h < end) if start > end else (start <= h < end)


def current_stage(days_overdue: int, offsets: list[int]) -> int | None:
    reached = [o for o in offsets if o <= days_overdue]
    return max(reached) if reached else None


def reminders_in_last_week(session: Session, buyer_id: int, now_utc: datetime) -> int:
    return session.scalar(
        select(func.count()).select_from(MessageLog).where(
            MessageLog.buyer_id == buyer_id,
            MessageLog.direction == "out",
            MessageLog.action == REMINDER_ACTION,
            MessageLog.created_at > now_utc - timedelta(days=7),
        )
    )


def due_stages(session: Session, company_id: int, as_of: date, offsets: list[int]) -> dict[int, list[tuple[Invoice, int]]]:
    """buyer_id -> [(invoice, stage)] for open invoices whose current stage hasn't been acted on."""
    invoices = session.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.balance_paise > 0)
    ).all()
    done = set(session.execute(
        select(Reminder.invoice_id, Reminder.stage).where(Reminder.company_id == company_id)
    ).all())
    out: dict[int, list[tuple[Invoice, int]]] = defaultdict(list)
    for inv in invoices:
        stage = current_stage((as_of - inv.due_date).days, offsets)
        if stage is not None and (inv.id, stage) not in done:
            out[inv.buyer_id].append((inv, stage))
    return out


def _to_ist(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError("pass an aware datetime (IST or UTC)")
    return now.astimezone(IST)


def run_reminders(session: Session, channel: Channel, now: datetime | None = None) -> RunReport:
    now = _to_ist(now)
    now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    report = RunReport(at=now)
    for company in session.scalars(select(Company)).all():
        _run_company(session, channel, company, now, now_utc, report)
    return report


def _run_company(session: Session, channel: Channel, company: Company, now: datetime, now_utc: datetime,
                 report: RunReport) -> None:
    policy = get_policy(session, company.id)
    as_of = now.date()
    pending = due_stages(session, company.id, as_of, policy.offsets)
    if not pending:
        return
    buyers = {b.id: b for b in session.scalars(
        select(Buyer).where(Buyer.id.in_(pending)).options(selectinload(Buyer.phones))
    )}
    quiet = in_quiet_hours(now, policy)
    holds = holds_for(session, company.id, as_of, now_utc)
    for buyer_id, items in sorted(pending.items(), key=lambda kv: buyers[kv[0]].ledger_name):
        buyer = buyers[buyer_id]
        disputed = [inv.number for inv, _ in items if inv.id in holds["dispute_invoices"]]
        items = [(inv, stage) for inv, stage in items if inv.id not in holds["dispute_invoices"]]
        numbers = [inv.number for inv, _ in items] or disputed

        def note(status: str, reason: str = "") -> None:
            report.results.append(BuyerResult(buyer.ledger_name, numbers, status, reason))

        if not items:
            note("skipped", "invoice under dispute")
            continue
        if not policy.enabled:
            note("skipped", "reminders are switched off")
            continue
        if buyer.paused:
            note("skipped", "paused")
            continue
        if buyer.id in holds["dispute_buyers"]:
            note("skipped", "dispute open")
            continue
        if buyer.id in holds["promise"]:
            note("deferred", "promised to pay")
            continue
        if buyer.id in holds["claim"]:
            note("deferred", "says already paid, being checked")
            continue
        if quiet:
            note("deferred", f"quiet hours ({policy.quiet_start_hour:02d}:00-{policy.quiet_end_hour:02d}:00 IST)")
            continue
        if not buyer.primary_phone:
            note("skipped", "no phone number on file")
            continue
        if reminders_in_last_week(session, buyer.id, now_utc) >= policy.max_per_week:
            note("deferred", f"weekly limit of {policy.max_per_week} reached")
            continue

        body = reminder_message(
            company_name=company.name, buyer_name=buyer.ledger_name, tone=buyer.tone,
            items=[ReminderLine(inv.number, inv.date, inv.due_date, inv.balance_paise) for inv, _ in items],
            as_of=as_of, synced_at_text=format_ist(company.last_synced_at),
        )
        meta = {"invoices": numbers, "stages": {inv.number: stage for inv, stage in items},
                "balances_paise": {inv.number: inv.balance_paise for inv, _ in items}}
        try:
            msg = send_to_buyer(session, channel, buyer, body, intent="payment_reminder",
                                action=REMINDER_ACTION, outcome="sent", meta=meta, now_utc=now_utc)
        except (ChannelError, PhoneNotAllowed) as e:  # raised before anything is logged or stored
            log.warning("reminder to %s failed: %s", buyer.ledger_name, e)
            note("failed", str(e))
            continue
        for inv, stage in items:
            session.add(Reminder(company_id=company.id, buyer_id=buyer.id, invoice_id=inv.id, stage=stage,
                                 status="sent", message_id=msg.id, created_at=now_utc))
        session.commit()
        note("sent")
    session.commit()


def on_payments_synced(session: Session, company_id: int, invoice_numbers: list[str], as_of: date) -> int:
    """Stop reminders for invoices that just received money. Returns how many stages were cancelled.

    Also records the outcome on the reminder messages that preceded the payment, which is what
    the dashboard's "recovered" figure and the future learning engine read.
    """
    if not invoice_numbers:
        return 0
    policy = get_policy(session, company_id)
    invoices = session.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.number.in_(invoice_numbers))
    ).all()
    cancelled = 0
    for inv in invoices:
        existing = {r.stage: r for r in session.scalars(select(Reminder).where(Reminder.invoice_id == inv.id))}
        stage = current_stage((as_of - inv.due_date).days, policy.offsets)
        if inv.balance_paise > 0 and stage is not None and stage not in existing:
            session.add(Reminder(company_id=company_id, buyer_id=inv.buyer_id, invoice_id=inv.id, stage=stage,
                                 status="cancelled", reason="payment received"))
            cancelled += 1
        outcome = "paid" if inv.balance_paise <= 0 else "part_paid"
        for r in existing.values():
            if r.status == "sent" and r.message_id:
                msg = session.get(MessageLog, r.message_id)
                if msg is not None:
                    paid = dict(msg.meta.get("paid", {}))
                    paid[inv.number] = outcome
                    msg.meta = {**msg.meta, "paid": paid}
                    msg.outcome = "paid" if all(paid.get(n) == "paid" for n in msg.meta.get("invoices", [])) \
                        else "part_paid"
    session.commit()
    return cancelled
