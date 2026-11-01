"""Reminder engine: stages, quiet hours, weekly cap, pause, stop-on-payment, message rules."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from revert.channels.base import Channel, ChannelError
from revert.channels.web_simulator import WebSimulatorChannel
from revert.core.jobs import sync_job
from revert.core.models import Buyer, BuyerPhone, Company, Invoice, MessageLog, Reminder
from revert.core.money import format_inr_exact
from revert.core.outbox import PhoneNotAllowed, send_to_buyer
from revert.core.reminders import current_stage, get_policy, in_quiet_hours, run_reminders
from revert.core.timeutil import IST, format_ist
from revert.seed.demo_company import MAHADEV, SHARMA
from revert.verticals.collections.templates import (
    FORBIDDEN_WORDS,
    ReminderLine,
    assistant_name,
    reminder_message,
)
from tests.conftest import AS_OF

D = date(2026, 11, 10)  # due date used by the hand-built scenarios


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


@pytest.fixture
def channel():
    return WebSimulatorChannel()


@pytest.fixture
def company(session):
    c = Company(name="Test Traders", last_synced_at=datetime(2026, 11, 1, 4, 30))
    session.add(c)
    session.flush()
    return c


def make_buyer(session, company, name="Asha Electricals", phone="+910000012345",
               invoices=(("INV-1", D, 10_000_00),), tone="standard"):
    b = Buyer(company_id=company.id, ledger_name=name, tone=tone)
    if phone:
        b.phones.append(BuyerPhone(phone=phone, is_primary=True))
    session.add(b)
    session.flush()
    for number, due, balance in invoices:
        session.add(Invoice(company_id=company.id, buyer_id=b.id, number=number, date=due - timedelta(days=30),
                            due_date=due, total_paise=balance, balance_paise=balance))
    session.commit()
    return b


def sent_messages(session, buyer_id=None):
    q = select(MessageLog).where(MessageLog.direction == "out", MessageLog.action == "reminder_sent")
    if buyer_id is not None:
        q = q.where(MessageLog.buyer_id == buyer_id)
    return session.scalars(q.order_by(MessageLog.id)).all()


# --- stage maths ---------------------------------------------------------------------------

@pytest.mark.parametrize("days,stage", [
    (-10, None), (-4, None), (-3, -3), (-1, -3), (0, 0), (6, 0), (7, 7), (14, 7), (15, 15), (29, 15),
    (30, 30), (200, 30),
])
def test_current_stage(days, stage):
    assert current_stage(days, [-3, 0, 7, 15, 30]) == stage


# --- quiet hours (8pm-9am IST) -------------------------------------------------------------

@pytest.mark.parametrize("hour,minute,quiet", [
    (19, 59, False), (20, 0, True), (23, 30, True), (0, 0, True), (2, 0, True), (8, 59, True),
    (9, 0, False), (13, 0, False),
])
def test_quiet_hours_boundaries(session, company, hour, minute, quiet):
    policy = get_policy(session, company.id)
    assert in_quiet_hours(at(D, hour, minute), policy) is quiet


def test_quiet_hours_reads_utc_times_as_ist(session, company):
    from datetime import timezone

    policy = get_policy(session, company.id)
    assert in_quiet_hours(datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc), policy)  # 20:00 IST
    assert not in_quiet_hours(datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc), policy)  # 09:00 IST


def test_nothing_is_sent_during_quiet_hours(session, company, channel):
    b = make_buyer(session, company)
    for when in (at(D, 20, 0), at(D, 23, 59), at(D + timedelta(days=1), 8, 59)):
        report = run_reminders(session, channel, when)
        assert [r.status for r in report.results] == ["deferred"]
        assert "quiet hours" in report.results[0].reason
    assert sent_messages(session) == []
    assert session.scalar(select(func.count()).select_from(Reminder)) == 0  # deferred, not dropped

    report = run_reminders(session, channel, at(D + timedelta(days=1), 9, 0))
    assert [r.status for r in report.results] == ["sent"]
    assert len(sent_messages(session, b.id)) == 1


# --- stages fire once, in order --------------------------------------------------------------

def test_each_stage_fires_once(session, company, channel):
    b = make_buyer(session, company)
    assert run_reminders(session, channel, at(D - timedelta(days=4), 10)).results == []  # too early

    for day, stage in ((D - timedelta(days=3), -3), (D, 0), (D + timedelta(days=7), 7)):
        assert [r.status for r in run_reminders(session, channel, at(day, 10)).results] == ["sent"]
        assert run_reminders(session, channel, at(day, 15)).results == []  # same stage, same day: nothing
        assert session.scalar(select(Reminder.status).where(Reminder.stage == stage)) == "sent"
    assert len(sent_messages(session, b.id)) == 3


def test_backlog_sends_only_the_latest_stage(session, company, channel):
    b = make_buyer(session, company, invoices=(("INV-OLD", D - timedelta(days=60), 5_000_00),))
    run_reminders(session, channel, at(D, 10))
    assert len(sent_messages(session, b.id)) == 1
    assert session.scalars(select(Reminder.stage)).all() == [30]


def test_one_message_per_buyer_for_several_invoices(session, company, channel):
    b = make_buyer(session, company, invoices=(
        ("INV-A", D - timedelta(days=20), 10_000_00),
        ("INV-B", D - timedelta(days=8), 2_500_50),
        ("INV-C", D + timedelta(days=2), 7_000_00),
    ))
    run_reminders(session, channel, at(D, 10))
    msgs = sent_messages(session, b.id)
    assert len(msgs) == 1
    body = msgs[0].body
    for n in ("INV-A", "INV-B", "INV-C"):
        assert n in body
    assert "₹2,500.50" in body and "Total: ₹19,500.50" in body
    assert session.scalar(select(func.count()).select_from(Reminder)) == 3


# --- weekly cap ------------------------------------------------------------------------------

def test_at_most_two_reminders_per_buyer_per_week(session, company, channel):
    policy = get_policy(session, company.id)
    policy.offsets = [0, 1, 2, 3]
    session.commit()
    b = make_buyer(session, company)

    statuses = [run_reminders(session, channel, at(D + timedelta(days=i), 10)).results[0].status for i in range(3)]
    assert statuses == ["sent", "sent", "deferred"]

    # 7 days after the first message it drops out of the window, so one more is allowed
    report = run_reminders(session, channel, at(D + timedelta(days=7), 10))
    assert report.results[0].status == "sent"
    assert len(sent_messages(session, b.id)) == 3


# --- pause, switch-off, phones, delivery failure -----------------------------------------------

def test_paused_buyer_gets_nothing_until_unpaused(session, company, channel):
    b = make_buyer(session, company)
    b.paused = True
    session.commit()
    report = run_reminders(session, channel, at(D, 10))
    assert report.results[0].status == "skipped" and report.results[0].reason == "paused by owner"
    assert sent_messages(session) == []

    b.paused = False
    session.commit()
    assert run_reminders(session, channel, at(D, 11)).results[0].status == "sent"


def test_switched_off_policy_sends_nothing(session, company, channel):
    make_buyer(session, company)
    get_policy(session, company.id).enabled = False
    session.commit()
    assert run_reminders(session, channel, at(D, 10)).results[0].status == "skipped"
    assert sent_messages(session) == []


def test_buyer_without_phone_is_skipped(session, company, channel):
    make_buyer(session, company, phone=None)
    report = run_reminders(session, channel, at(D, 10))
    assert report.results[0].reason == "no phone number on file"


def test_only_stored_numbers_can_be_messaged(session, company, channel):
    b = make_buyer(session, company)
    with pytest.raises(PhoneNotAllowed):
        send_to_buyer(session, channel, b, "hello", intent="x", action="y", phone="+919999999999")
    assert session.scalar(select(func.count()).select_from(MessageLog)) == 0


def test_failed_delivery_keeps_the_stage_open(session, company, channel):
    class Down(Channel):
        name = "down"

        def send_text(self, phone, body):
            raise ChannelError("network down")

    b = make_buyer(session, company)
    assert run_reminders(session, Down(), at(D, 10)).results[0].status == "failed"
    assert session.scalar(select(func.count()).select_from(Reminder)) == 0
    assert run_reminders(session, channel, at(D, 11)).results[0].status == "sent"
    assert len(sent_messages(session, b.id)) == 1


# --- stop on payment (against the mock Tally, through a real sync) ----------------------------

MORNING = at(AS_OF, 10)


def _buyer(session, name):
    return session.scalar(select(Buyer).where(Buyer.ledger_name == name))


def test_full_payment_synced_before_the_run_means_no_reminder(session, connector, dataset, channel):
    assert sync_job(session, connector, AS_OF).ok
    sharma = _buyer(session, SHARMA)
    late = session.scalar(select(Invoice).where(Invoice.buyer_id == sharma.id, Invoice.due_date < AS_OF,
                                                Invoice.balance_paise > 0))
    dataset.add_receipt(SHARMA, late.balance_paise, AS_OF, invoice_number=late.number)
    res = sync_job(session, connector, AS_OF)
    assert late.number in res.paid_invoices

    report = run_reminders(session, channel, MORNING)
    assert SHARMA not in [r.buyer for r in report.sent]
    assert all(late.number not in m.body for m in sent_messages(session, sharma.id))


def test_part_payment_stops_that_invoice_but_not_the_others(session, connector, dataset, channel):
    assert sync_job(session, connector, AS_OF).ok
    dataset.add_receipt(MAHADEV, 10_000_00, AS_OF, invoice_number="INV-1056")
    assert "INV-1056" in sync_job(session, connector, AS_OF).paid_invoices
    cancelled = session.scalar(select(Reminder).join(Invoice).where(Invoice.number == "INV-1056"))
    assert cancelled.status == "cancelled" and cancelled.reason == "payment received"

    run_reminders(session, channel, MORNING)
    [msg] = sent_messages(session, _buyer(session, MAHADEV).id)
    assert "INV-1040" in msg.body and "INV-1071" in msg.body
    assert "INV-1056" not in msg.body


def test_payment_after_a_reminder_records_the_outcome_and_stops_further_reminders(session, connector, dataset,
                                                                                   channel):
    assert sync_job(session, connector, AS_OF).ok
    run_reminders(session, channel, MORNING)
    mahadev = _buyer(session, MAHADEV)
    [msg] = sent_messages(session, mahadev.id)
    assert msg.outcome == "sent"

    for number in ("INV-1040", "INV-1056", "INV-1071"):
        inv = session.scalar(select(Invoice).where(Invoice.number == number))
        dataset.add_receipt(MAHADEV, inv.balance_paise, AS_OF, invoice_number=number)
    sync_job(session, connector, AS_OF)
    session.refresh(msg)
    assert msg.outcome == "paid"

    # weeks later, every later stage stays silent: nothing is owed
    for days in (15, 30, 60):
        run_reminders(session, channel, at(AS_OF + timedelta(days=days), 10))
    assert len(sent_messages(session, mahadev.id)) == 1


# --- message content rules ---------------------------------------------------------------------

@pytest.mark.parametrize("tone", ["gentle", "standard", "firm"])
@pytest.mark.parametrize("days_late", [-3, 0, 7, 45])
def test_message_identifies_assistant_quotes_db_amounts_and_never_threatens(tone, days_late):
    as_of = date(2026, 11, 10)
    items = [ReminderLine("INV-9", date(2026, 9, 1), as_of - timedelta(days=days_late), 27_848_40)]
    body = reminder_message(company_name="Shree Ganesh Electricals", buyer_name="Asha", tone=tone,
                            items=items, as_of=as_of, synced_at_text="10 Nov 2026, 09:15 AM IST")
    assert "Shree Ganesh Electricals's assistant" in body
    assert "₹27,848.40" in body
    assert "updated 10 Nov 2026, 09:15 AM IST" in body
    lower = body.lower()
    assert not [w for w in FORBIDDEN_WORDS if w in lower]


def test_every_demo_reminder_quotes_only_db_numbers_and_sync_time(session, connector, channel):
    assert sync_job(session, connector, AS_OF).ok
    company = session.scalar(select(Company))
    run_reminders(session, channel, MORNING)
    msgs = sent_messages(session)
    assert len(msgs) >= 10
    for m in msgs:
        assert assistant_name(company.name) in m.body
        assert f"updated {format_ist(company.last_synced_at)}" in m.body
        for number, balance in m.meta["balances_paise"].items():
            inv = session.scalar(select(Invoice).where(Invoice.number == number))
            assert inv.balance_paise == balance
            assert f"{number} dated" in m.body and format_inr_exact(balance) in m.body
        phones = {p.phone for p in m.buyer.phones}
        assert m.phone in phones
