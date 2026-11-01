"""The AI agent end to end: dealer message in -> tools -> reply out, on the demo books.

Runs on the offline rules engine (conftest sets LLM_PROVIDER=rules), so no network.
"""
import re
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from revert.agent import guard
from revert.channels.web_simulator import WebSimulatorChannel
from revert.core.conversation import receive_message
from revert.core.jobs import reminder_job, sync_job
from revert.core.models import (
    Buyer,
    Dispute,
    Invoice,
    InvoiceLine,
    MessageLog,
    PaymentClaim,
    PaymentPromise,
    Receipt,
    Reminder,
    StockItem,
)
from revert.core.money import format_inr_exact
from revert.core.timeutil import IST, format_ist
from revert.seed.demo_company import DEMO_PREFS, DISPUTED_INVOICE, MAHADEV, SHARMA, VENKATESH
from revert.verticals.collections import replies
from tests.conftest import AS_OF

CH = WebSimulatorChannel()
MORNING = datetime.combine(AS_OF, time(11, 0), IST)


def utc(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def books(session, connector):
    assert sync_job(session, connector, AS_OF).ok
    for b in session.scalars(select(Buyer)):
        prefs = DEMO_PREFS[b.ledger_name]
        b.language, b.tone = prefs["language"], prefs["tone"]
    session.commit()
    return session


def buyer(session, name) -> Buyer:
    return session.scalar(select(Buyer).where(Buyer.ledger_name == name))


def say(session, name, text, at=MORNING):
    b = buyer(session, name)
    res = receive_message(session, CH, b.primary_phone, text, now_utc=utc(at))
    return res.inbound, res.reply


def synced(session):
    return format_ist(buyer(session, SHARMA).company.last_synced_at)


# --- already paid ---------------------------------------------------------------------------

def test_already_paid_without_record_asks_for_utr_flags_owner_and_holds_reminders(books):
    inbound, reply = say(books, MAHADEV, "I already paid 20k last week")
    assert (inbound.intent, inbound.action, inbound.outcome) == ("already_paid", "log_payment_claim", "awaiting_owner")
    assert "UTR" in reply.body and "screenshot" in reply.body
    claim = books.scalar(select(PaymentClaim))
    assert claim.status == "open" and claim.buyer.ledger_name == MAHADEV
    report = reminder_job(books, CH, MORNING)
    mahadev = [r for r in report.results if r.buyer == MAHADEV][0]
    assert mahadev.status == "deferred" and "already paid" in mahadev.reason
    # after the hold expires, reminders go out again
    later = reminder_job(books, CH, MORNING + timedelta(days=4))
    assert MAHADEV in [r.buyer for r in later.sent]


def test_already_paid_with_known_utr_confirms_from_the_books(books):
    r = books.scalar(select(Receipt).join(Buyer).where(Buyer.ledger_name == SHARMA, Receipt.narration.like("%UTR%"))
                     .order_by(Receipt.date.desc()))
    utr = r.narration.split()[-1]
    inbound, reply = say(books, SHARMA, f"Payment done, UTR {utr}")
    assert (inbound.intent, inbound.outcome) == ("already_paid", "answered")
    assert format_inr_exact(r.amount_paise) in reply.body and utr in reply.body
    assert synced(books) in reply.body


def test_someone_elses_utr_is_not_revealed(books):
    other = books.scalar(select(Receipt).join(Buyer).where(Buyer.ledger_name != SHARMA,
                                                           Receipt.narration.like("%UTR%")))
    utr = other.narration.split()[-1]
    inbound, reply = say(books, SHARMA, f"paid, utr {utr}")
    assert inbound.outcome == "awaiting_owner" and format_inr_exact(other.amount_paise) not in reply.body


def test_claim_is_matched_when_the_payment_syncs(books, dataset, connector):
    say(books, MAHADEV, "paid yesterday, UTR HDFC000123456789")
    assert books.scalar(select(PaymentClaim)).status == "open"
    dataset.add_receipt(MAHADEV, 22_770_00, AS_OF, narration="NEFT UTR HDFC000123456789")
    sync_job(books, connector, AS_OF)
    assert books.scalar(select(PaymentClaim)).status == "matched"


# --- promise to pay -------------------------------------------------------------------------

def test_split_promise_is_logged_and_holds_reminders(books):
    inbound, reply = say(books, MAHADEV, "50k now, rest on 15th")
    assert (inbound.intent, inbound.action, inbound.outcome) == ("promise_to_pay", "log_promise", "promise_logged")
    rows = books.scalars(select(PaymentPromise).order_by(PaymentPromise.due_date)).all()
    assert [(p.kind, p.amount_paise, p.cumulative_paise, p.due_date.isoformat()) for p in rows] == [
        ("amount", 50_000_00, 50_000_00, "2026-10-30"),
        ("rest", 30_000_00, 80_000_00, "2026-11-15"),
    ]
    assert "₹50,000" in reply.body and "₹30,000" in reply.body and "15 Nov 2026" in reply.body
    assert synced(books) in reply.body
    report = reminder_job(books, CH, MORNING)
    assert [r.status for r in report.results if r.buyer == MAHADEV] == ["deferred"]


def test_promise_without_a_date_asks_for_one(books):
    inbound, reply = say(books, MAHADEV, "will pay 20000")
    assert (inbound.action, inbound.outcome) == ("ask_date", "awaiting_buyer")
    assert "date" in reply.body and books.scalar(select(PaymentPromise)) is None


def test_hindi_promise_gets_a_hindi_reply(books):
    inbound, reply = say(books, MAHADEV, "kal paisa bhej dunga")
    assert inbound.intent == "promise_to_pay"
    assert "Shree Ganesh Electricals का असिस्टेंट" in reply.body and "₹80,000" in reply.body
    assert books.scalar(select(PaymentPromise)).due_date == AS_OF + timedelta(days=1)


def test_kept_promise_is_marked_kept(books, dataset, connector):
    say(books, MAHADEV, "I'll pay everything tomorrow")
    dataset.add_receipt(MAHADEV, 80_000_00, AS_OF + timedelta(days=1))
    sync_job(books, connector, AS_OF + timedelta(days=1))
    assert books.scalar(select(PaymentPromise)).status == "kept"


def test_broken_promise_gets_exactly_one_followup(books):
    say(books, MAHADEV, "I'll pay tomorrow")
    report = reminder_job(books, CH, MORNING + timedelta(days=2))
    assert report.followups == [MAHADEV]
    p = books.scalar(select(PaymentPromise))
    assert p.status == "broken" and p.followed_up_at is not None
    msg = books.scalar(select(MessageLog).where(MessageLog.action == "promise_followup"))
    # Mahadev prefers Hindi, so the follow-up (which has no dealer text to detect from) is in Hindi
    assert "₹80,000" in msg.body and "31 Oct 2026" in msg.body and "Shree Ganesh Electricals का असिस्टेंट" in msg.body
    assert reminder_job(books, CH, MORNING + timedelta(days=2, hours=1)).followups == []


def test_broken_promise_followup_waits_for_morning(books):
    say(books, MAHADEV, "I'll pay tomorrow")
    night = datetime.combine(AS_OF + timedelta(days=2), time(22, 0), IST)
    assert reminder_job(books, CH, night).followups == []
    assert reminder_job(books, CH, night + timedelta(hours=12)).followups == [MAHADEV]


# --- disputes -------------------------------------------------------------------------------

def test_dispute_logs_notifies_owner_and_stops_reminders_for_that_invoice(books):
    inbound, reply = say(books, VENKATESH, "INV-1043 rate is wrong, our PO said 1150 for the RCCB")
    assert (inbound.intent, inbound.action, inbound.outcome) == ("dispute", "log_dispute", "awaiting_owner")
    d = books.scalar(select(Dispute))
    assert d.invoice.number == DISPUTED_INVOICE and d.status == "open"
    assert DISPUTED_INVOICE in reply.body and "ಸಂಸ್ಥೆಯ ಸಹಾಯಕ" not in reply.body  # wrote in English
    reminder_job(books, CH, MORNING)
    assert books.scalar(select(Reminder).join(Invoice).where(Invoice.number == DISPUTED_INVOICE)) is None
    d.status = "resolved"
    books.commit()
    reminder_job(books, CH, MORNING + timedelta(days=1))
    assert books.scalar(select(Reminder).join(Invoice).where(Invoice.number == DISPUTED_INVOICE)) is not None


def test_dispute_without_invoice_holds_all_reminders_for_the_buyer(books):
    say(books, MAHADEV, "last delivery was short, 2 coils missing")
    assert books.scalar(select(Dispute)).invoice_id is None
    report = reminder_job(books, CH, MORNING)
    assert [(r.status, r.reason) for r in report.results if r.buyer == MAHADEV] == [("skipped", "dispute open")]


# --- invoices, statement, balance -----------------------------------------------------------

def test_send_invoice_lists_lines_and_totals_from_the_db(books):
    inv = books.scalar(select(Invoice).where(Invoice.number == "INV-1040"))
    inbound, reply = say(books, MAHADEV, "please send invoice 1040")
    assert (inbound.intent, inbound.action) == ("send_invoice", "send_invoice")
    for line in inv.lines:
        assert line.item_name in reply.body and format_inr_exact(line.amount_paise) in reply.body
    assert format_inr_exact(inv.total_paise) in reply.body and format_inr_exact(inv.balance_paise) in reply.body


def test_another_dealers_invoice_is_not_shown(books):
    inbound, reply = say(books, SHARMA, "send invoice 1040")  # INV-1040 is Mahadev's
    assert "couldn't find" in reply.body and "INV-1040" not in reply.body


def test_statement_and_balance(books):
    b = buyer(books, MAHADEV)
    _, reply = say(books, MAHADEV, "send my statement")
    assert "₹80,000" in reply.body and "Last payments" in reply.body or "पिछले" in reply.body
    _, reply = say(books, SHARMA, "how much do I owe?")
    total = sum(i.balance_paise for i in buyer(books, SHARMA).invoices if i.balance_paise > 0)
    assert f"Total pending: {format_inr_exact(total)}" in reply.body
    assert b.language == "hi"


# --- stock and orders -----------------------------------------------------------------------

def test_stock_answer_comes_from_synced_stock(books):
    item = books.scalar(select(StockItem).where(StockItem.name == "MCB SP 16A C-Curve"))
    inbound, reply = say(books, SHARMA, "Is 16A MCB available?")
    assert (inbound.intent, inbound.outcome) == ("stock_query", "answered")
    assert f"{int(item.qty)} Nos in stock" in reply.body and format_inr_exact(item.rate_paise) in reply.body
    assert "Stock as per our books" in reply.body
    assert "pending" not in reply.body.lower()  # no credit-aware selling: stock replies never mention dues


def test_out_of_stock_and_unknown_items(books):
    _, reply = say(books, SHARMA, "changeover switch available?")
    assert "out of stock" in reply.body
    inbound, reply = say(books, SHARMA, "do you have solar panels? need 10 pcs of mcb boxes")
    assert inbound.intent in ("stock_query", "order_request")


def test_order_goes_to_owner_with_stock_info(books):
    inbound, reply = say(books, SHARMA, "need 50 nos 32A MCB")
    assert (inbound.intent, inbound.outcome) == ("order_request", "awaiting_owner")
    assert "MCB SP 32A C-Curve" in reply.body and "MCB DP 32A C-Curve" in reply.body


# --- other intents --------------------------------------------------------------------------

def test_opt_out_pauses_reminders(books):
    inbound, reply = say(books, SHARMA, "stop messaging me")
    assert inbound.intent == "opt_out" and buyer(books, SHARMA).paused


def test_identity_is_never_human(books):
    _, reply = say(books, SHARMA, "are you a bot or a real person?")
    assert "automated assistant, not a person" in reply.body


def test_kannada_reply(books):
    _, reply = say(books, VENKATESH, "ನನ್ನ ಬಾಕಿ ಎಷ್ಟು?")
    assert "Shree Ganesh Electricals ಸಂಸ್ಥೆಯ ಸಹಾಯಕ" in reply.body and "ಒಟ್ಟು ಬಾಕಿ" in reply.body


def test_upset_dealer_gets_an_apology_and_the_owner_is_told(books):
    inbound, reply = say(books, SHARMA, "why are you disturbing me!! how much is pending")
    assert "Sorry for the trouble." in reply.body and inbound.outcome == "awaiting_owner"


def test_every_message_is_logged_with_intent_action_outcome(books):
    for text in ("hi", "send statement", "INV-1043 is wrong", "random words"):
        say(books, VENKATESH, text)
    for m in books.scalars(select(MessageLog)):
        assert m.intent and m.action and m.outcome, (m.direction, m.body)


# --- hard rule: no invented numbers ---------------------------------------------------------

MESSAGES = [
    "I already paid", "paid 5000 yesterday", "send my statement", "how much do I owe?", "send the bill",
    "I'll pay Friday", "10k today, balance next week", "is 16A MCB available?", "RCCB rate?", "need 20 nos 6A MCB",
    "wire 2.5 sq mm stock?", "मेरा कितना बाकी है?", "ನನ್ನ ಬಾಕಿ ಎಷ್ಟು?", "hisab bhejo", "kal paisa bhej dunga",
    "are you a bot?", "rate is wrong in last bill",
]
_RUPEES = re.compile(r"₹\d{1,3}(?:,\d{2,3})*(?:\.\d{2})?")


def _db_amounts(session, buyer_id) -> set[str]:
    out = set()
    for inv in session.scalars(select(Invoice).where(Invoice.buyer_id == buyer_id)):
        out |= {inv.total_paise, inv.subtotal_paise, inv.tax_paise, inv.balance_paise, inv.paid_paise}
        out |= {l.amount_paise for l in session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))}
        out |= {l.rate_paise for l in session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))}
    out |= {r.amount_paise for r in session.scalars(select(Receipt).where(Receipt.buyer_id == buyer_id))}
    out |= {s.rate_paise for s in session.scalars(select(StockItem))}
    out.add(sum(i.balance_paise for i in session.scalars(select(Invoice).where(Invoice.buyer_id == buyer_id,
                                                                                Invoice.balance_paise > 0))))
    out |= {p.amount_paise for p in session.scalars(select(PaymentPromise).where(PaymentPromise.buyer_id == buyer_id))}
    return {format_inr_exact(p) for p in out}


def test_no_invented_numbers_across_every_dealer_and_intent(books):
    sync_text = synced(books)
    checked = 0
    for b in books.scalars(select(Buyer).order_by(Buyer.id)).all():
        for text in MESSAGES:
            inbound, reply = say(books, b.ledger_name, text)
            body = reply.body
            assert inbound.meta["guard"] == [], (b.ledger_name, text, inbound.meta["guard"])
            allowed = _db_amounts(books, b.id) | set(_RUPEES.findall(text))
            for amount in _RUPEES.findall(body):
                assert amount in allowed, (b.ledger_name, text, amount, body)
                checked += 1
            if _RUPEES.search(body):
                assert sync_text in body, (b.ledger_name, text, body)  # every money reply carries the sync time
            assert replies.ASSISTANT[reply_lang(body)].format(company="Shree Ganesh Electricals") in body
            assert not any(w in body.lower() for w in ("legal", "court", "lawyer", "police"))
    assert checked > 300


def reply_lang(body: str) -> str:
    if re.search(r"[ಀ-೿]", body):
        return "kn"
    if re.search(r"[ऀ-ॿ]", body):
        return "hi"
    return "en"


def test_guard_blocks_a_reply_with_an_invented_number_and_undoes_its_writes(books, monkeypatch):
    bad = dict(replies.T["promise_amount"])
    bad["en"] = "• {amount} by {date} (or ₹99,999 if easier)"
    monkeypatch.setitem(replies.T, "promise_amount", bad)
    inbound, reply = say(books, MAHADEV, "I will pay 20000 on Monday")
    assert inbound.action == "guard_blocked" and inbound.outcome == "awaiting_owner"
    assert "99999" in inbound.meta["guard"][0]
    assert "₹" not in reply.body and "assistant" in reply.body
    assert books.scalar(select(PaymentPromise)) is None  # the promise write was rolled back
    assert books.get(MessageLog, inbound.id) is not None  # but the inbound message stays logged


def test_guard_rules():
    ok = "Namaste X,\nTotal pending: ₹1,200\n— Acme's assistant"
    assert guard.check_reply(ok, ["₹1,200"], "", "Acme", "en") == []
    assert "numbers not from" in guard.check_reply(ok, ["₹1,300"], "", "Acme", "en")[0]
    assert guard.check_reply(ok, [], "I owe 1,200?", "Acme", "en") == []  # the dealer wrote it
    assert any("forbidden" in p for p in guard.check_reply(ok + " court", ["₹1,200"], "", "Acme", "en"))
    assert any("signed" in p for p in guard.check_reply("Total ₹1,200", ["₹1,200"], "", "Acme", "en"))
    assert any("digits" in p for p in guard.check_reply(ok + " ५", ["₹1,200"], "", "Acme", "en"))


def test_templates_have_no_literal_digits():
    for key, langs in replies.T.items():
        for lang, text in langs.items():
            assert not re.search(r"\d", re.sub(r"\{[^}]*\}", "", text)), (key, lang)
            assert set(langs) == {"en", "hi", "kn"}, key
