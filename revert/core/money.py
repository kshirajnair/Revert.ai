"""Money is stored as integer paise everywhere. Never floats."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def rupees_to_paise(value: str | int | float | Decimal) -> int:
    d = Decimal(str(value).replace(",", "").strip() or "0")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def _indian_group(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_inr(paise: int, show_paise: bool = False) -> str:
    """12345678 paise -> '₹1,23,456' (or '₹1,23,456.78' with show_paise)."""
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(paise), 100)
    if not show_paise and rem >= 50:
        rupees += 1
    out = f"{sign}₹{_indian_group(str(rupees))}"
    if show_paise:
        out += f".{rem:02d}"
    return out


def format_inr_exact(paise: int) -> str:
    """Like format_inr, but keeps paise when there are any, so a quoted amount is never rounded."""
    return format_inr(paise, show_paise=paise % 100 != 0)
