"""Reminder and reply templates for the collections vertical.

Rules these templates follow (and tests check):
- open with "<Business name>'s assistant", never posing as a person;
- every amount comes from the caller (read from the DB), never computed or invented here,
  and every message with money ends with the data's last-synced time;
- no threats and no mention of legal action, in any tone.

English only for now. Hindi and Kannada replies arrive with the agent in Phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from revert.core.money import format_inr_exact

# Words that must never appear in an automated message. Checked by tests for every tone.
FORBIDDEN_WORDS = ("legal", "court", "lawyer", "advocate", "police", "notice", "lawsuit",
                   "blacklist", "consequence", "penalty", "recover through")


@dataclass(frozen=True)
class ReminderLine:
    number: str
    invoice_date: date
    due_date: date
    balance_paise: int


def assistant_name(company_name: str) -> str:
    return f"{company_name}'s assistant"


# (singular, plural) lead-in, by message kind and tone
_LEAD = {
    "upcoming": {
        "gentle": ("A friendly heads-up that this payment is coming up:",
                   "A friendly heads-up that these payments are coming up:"),
        "standard": ("A reminder that this payment is due soon:", "A reminder that these payments are due soon:"),
        "firm": ("Please note this payment is due shortly:", "Please note these payments are due shortly:"),
    },
    "due_today": {
        "gentle": ("Just a gentle reminder that this payment is due today:",
                   "Just a gentle reminder that these payments are due today:"),
        "standard": ("This payment is due today:", "These payments are due today:"),
        "firm": ("This payment is due today and should be cleared without delay:",
                 "These payments are due today and should be cleared without delay:"),
    },
    "overdue": {
        "gentle": ("Just a gentle reminder that this payment is still pending:",
                   "Just a gentle reminder that these payments are still pending:"),
        "standard": ("Our records show this payment is now overdue:", "Our records show these payments are now overdue:"),
        "firm": ("This payment is overdue and needs your immediate attention:",
                 "These payments are overdue and need your immediate attention:"),
    },
}

_CLOSE = {
    "upcoming": {
        "gentle": "Thank you for your continued business!",
        "standard": "Thank you!",
        "firm": "Please make sure it is paid on time.",
    },
    "due": {
        "gentle": "Whenever convenient, please arrange the payment. Thank you for your continued business!",
        "standard": "Please arrange the payment at the earliest, or reply here if you have any questions.",
        "firm": "Please make the payment today, or reply with the date you will pay.",
    },
}


def _line(item: ReminderLine, as_of: date) -> str:
    days = (as_of - item.due_date).days
    amount = format_inr_exact(item.balance_paise)
    if days < 0:
        when = f"due on {item.due_date:%d %b}"
    elif days == 0:
        when = "due today"
    else:
        when = f"due on {item.due_date:%d %b} ({days} day{'s' if days != 1 else ''} ago)"
    return f"• {item.number} dated {item.invoice_date:%d %b %Y}: {amount}, {when}"


def reminder_message(*, company_name: str, buyer_name: str, tone: str, items: list[ReminderLine],
                     as_of: date, synced_at_text: str) -> str:
    if not items:
        raise ValueError("a reminder needs at least one invoice")
    tone = tone if tone in _CLOSE["due"] else "standard"
    most_late = max((as_of - i.due_date).days for i in items)
    kind = "upcoming" if most_late < 0 else "due_today" if most_late == 0 else "overdue"
    lead = _LEAD[kind][tone][0 if len(items) == 1 else 1]
    close = _CLOSE["upcoming" if kind == "upcoming" else "due"][tone]

    parts = [f"Namaste {buyer_name}, this is {assistant_name(company_name)}.", "", lead]
    parts += [_line(i, as_of) for i in sorted(items, key=lambda i: i.due_date)]
    if len(items) > 1:
        parts.append(f"Total: {format_inr_exact(sum(i.balance_paise for i in items))}")
    parts += ["", close, "Already paid? Just reply here and we'll check.",
              f"Amounts as per our books, updated {synced_at_text}."]
    return "\n".join(parts)


def handoff_reply(*, company_name: str, buyer_name: str) -> str:
    return (f"Thank you, {buyer_name}. This is {assistant_name(company_name)}. "
            "I've passed your message to the team and someone will get back to you shortly.")
