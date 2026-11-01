"""Parsing amounts, dates, references and promise installments from dealer messages."""
from datetime import date

import pytest

from revert.agent.parse import Installment, find_amounts, find_dates, installments, invoice_refs, payment_refs
from revert.agent.understand import classify_rules, detect_language

TODAY = date(2026, 10, 30)  # a Friday


@pytest.mark.parametrize("text,paise", [
    ("50k", 50_000_00),
    ("₹50,000", 50_000_00),
    ("Rs. 22770", 22_770_00),
    ("1.5 lakh", 1_50_000_00),
    ("1,25,000", 1_25_000_00),
    ("50 hazar", 50_000_00),
    ("20000 rupees", 20_000_00),
    ("₹22,770.50", 22_770_50),
    ("50 हजार", 50_000_00),
    ("2 ಲಕ್ಷ", 2_00_000_00),
])
def test_amounts(text, paise):
    assert [f.value for f in find_amounts(text, TODAY)] == [paise]


@pytest.mark.parametrize("text", [
    "INV-1043 is wrong", "bill 1043", "UTR SBIN123456789012", "on 15th", "15 Nov", "need 50 nos", "2 days",
])
def test_not_amounts(text):
    assert find_amounts(text, TODAY) == []


@pytest.mark.parametrize("text,expected", [
    ("today", date(2026, 10, 30)),
    ("abhi", date(2026, 10, 30)),
    ("tomorrow", date(2026, 10, 31)),
    ("kal", date(2026, 10, 31)),
    ("ನಾಳೆ", date(2026, 10, 31)),
    ("परसों", date(2026, 11, 1)),
    ("day after tomorrow", date(2026, 11, 1)),
    ("Friday", date(2026, 10, 30)),
    ("monday", date(2026, 11, 2)),
    ("शुक्रवार", date(2026, 10, 30)),
    ("15th", date(2026, 11, 15)),
    ("30th", date(2026, 10, 30)),
    ("5 tarikh", date(2026, 11, 5)),
    ("15 Nov", date(2026, 11, 15)),
    ("Nov 15", date(2026, 11, 15)),
    ("5 Jan", date(2027, 1, 5)),
    ("15/11", date(2026, 11, 15)),
    ("next week", date(2026, 11, 2)),
    ("month end", date(2026, 10, 31)),
    ("pay by 20", date(2026, 11, 20)),
])
def test_dates(text, expected):
    assert [f.value for f in find_dates(text, TODAY)] == [expected]


def test_refs():
    assert invoice_refs("INV-1043 and bill no 1040") == ["1040", "1043"] or \
        sorted(invoice_refs("INV-1043 and bill no 1040")) == ["1040", "1043"]
    assert payment_refs("UTR SBIN123456789012") == ["SBIN123456789012"]
    assert payment_refs("upi ref 861946100998") == ["861946100998"]
    assert payment_refs("INV-1043") == []


@pytest.mark.parametrize("text,expected", [
    ("50k now, rest on 15th", [Installment(50_000_00, TODAY), Installment(None, date(2026, 11, 15), rest=True)]),
    ("I'll pay Friday", [Installment(None, TODAY)]),
    ("will pay 20000 by monday", [Installment(20_000_00, date(2026, 11, 2))]),
    ("will pay 20000", [Installment(20_000_00, None)]),
    ("kal 30 hazar aur baaki agle hafte", [Installment(30_000_00, date(2026, 10, 31)),
                                           Installment(None, date(2026, 11, 2), rest=True)]),
])
def test_installments(text, expected):
    assert installments(text, TODAY) == expected


@pytest.mark.parametrize("text,lang", [
    ("I already paid", "en"), ("kal paisa bhej dunga", "hi"), ("मेरा कितना बाकी है", "hi"),
    ("ನಾಳೆ ಪಾವತಿ ಮಾಡುತ್ತೇನೆ", "kn"), ("naale kodtini saar", "kn"),
])
def test_language(text, lang):
    assert detect_language(text) == lang


@pytest.mark.parametrize("text,intent", [
    ("I already paid 20k last week", "already_paid"),
    ("payment done, UTR SBIN123456789012", "already_paid"),
    ("bhej diya kal", "already_paid"),
    ("I'll pay Friday", "promise_to_pay"),
    ("50k now, rest on 15th", "promise_to_pay"),
    ("kal paisa bhej dunga", "promise_to_pay"),
    ("ನಾಳೆ ಪಾವತಿ ಮಾಡುತ್ತೇನೆ", "promise_to_pay"),
    ("INV-1043 rate is wrong", "dispute"),
    ("short delivery of 5 MCBs in last bill", "dispute"),
    ("send invoice 1043", "send_invoice"),
    ("please share the bill", "send_invoice"),
    ("send my statement", "send_statement"),
    ("hisab bhejo", "send_statement"),
    ("how much do I owe?", "balance_query"),
    ("मेरा कितना बाकी है?", "balance_query"),
    ("is 16A MCB available", "stock_query"),
    ("RCCB rate?", "stock_query"),
    ("need 50 nos 32A MCB", "order_request"),
    ("stop messaging me", "opt_out"),
    ("are you a bot?", "smalltalk"),
    ("thanks", "smalltalk"),
    ("what's the weather", "unclear"),
])
def test_rules_intents(text, intent):
    assert classify_rules(text, TODAY).intent == intent
