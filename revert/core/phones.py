"""Phone normalisation to E.164. Revert only ever messages numbers stored against a buyer."""
from __future__ import annotations

import re


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits


def is_demo_number(phone: str) -> bool:
    """Indian numbers never start with 0 after +91. The seed uses +91 00000 xxxxx on purpose, so
    such numbers are simulator-only and are never handed to WhatsApp."""
    return phone.startswith("+910")
    return "+" + digits


def is_demo_number(phone: str) -> bool:
    """Indian numbers never start with 0 after +91. The seed uses +91 00000 xxxxx on purpose, so
    such numbers are simulator-only and are never handed to WhatsApp."""
    return phone.startswith("+910")
