"""Receivables ageing.

days_overdue = as_of - due_date
    < 0   -> not_due  (current)
    0-30  -> "0-30"   (due today counts as 0 days overdue)
    31-60 -> "31-60"
    61-90 -> "61-90"
    > 90  -> "90+"
Only invoices with a positive balance are aged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from revert.core.models import Buyer, Invoice


class Bucket(str, Enum):
    NOT_DUE = "not_due"
    D0_30 = "0-30"
    D31_60 = "31-60"
    D61_90 = "61-90"
    D90_PLUS = "90+"


OVERDUE_BUCKETS = (Bucket.D0_30, Bucket.D31_60, Bucket.D61_90, Bucket.D90_PLUS)


def days_overdue(due_date: date, as_of: date) -> int:
    return (as_of - due_date).days


def bucket_for(days: int) -> Bucket:
    if days < 0:
        return Bucket.NOT_DUE
    if days <= 30:
        return Bucket.D0_30
    if days <= 60:
        return Bucket.D31_60
    if days <= 90:
        return Bucket.D61_90
    return Bucket.D90_PLUS


@dataclass(frozen=True)
class InvoiceAgeing:
    invoice_id: int
    number: str
    buyer_id: int
    date: date
    due_date: date
    total_paise: int
    balance_paise: int
    days_overdue: int
    bucket: Bucket


@dataclass
class BuyerAgeing:
    buyer_id: int
    name: str
    credit_limit_paise: int | None
    buckets: dict[Bucket, int] = field(default_factory=lambda: {b: 0 for b in Bucket})
    invoices: list[InvoiceAgeing] = field(default_factory=list)

    @property
    def total_outstanding_paise(self) -> int:
        return sum(self.buckets.values())

    @property
    def total_overdue_paise(self) -> int:
        return sum(self.buckets[b] for b in OVERDUE_BUCKETS)

    @property
    def oldest_days_overdue(self) -> int:
        """Max days overdue among open invoices; negative if nothing is overdue yet."""
        return max((i.days_overdue for i in self.invoices), default=0)


def age_invoice(inv: Invoice, as_of: date) -> InvoiceAgeing:
    d = days_overdue(inv.due_date, as_of)
    return InvoiceAgeing(inv.id, inv.number, inv.buyer_id, inv.date, inv.due_date,
                         inv.total_paise, inv.balance_paise, d, bucket_for(d))


def age_invoices(invoices: list[Invoice], as_of: date) -> list[InvoiceAgeing]:
    return [age_invoice(i, as_of) for i in invoices if i.balance_paise > 0]


def buyer_ageing(buyer: Buyer, as_of: date) -> BuyerAgeing:
    ba = BuyerAgeing(buyer.id, buyer.ledger_name, buyer.credit_limit_paise)
    for ia in sorted(age_invoices(buyer.invoices, as_of), key=lambda x: -x.days_overdue):
        ba.buckets[ia.bucket] += ia.balance_paise
        ba.invoices.append(ia)
    return ba


def ageing_report(session: Session, company_id: int, as_of: date) -> list[BuyerAgeing]:
    """All buyers with something outstanding, most-overdue first."""
    buyers = session.scalars(
        select(Buyer).where(Buyer.company_id == company_id).options(selectinload(Buyer.invoices))
    ).all()
    rows = [buyer_ageing(b, as_of) for b in buyers]
    rows = [r for r in rows if r.total_outstanding_paise > 0]
    rows.sort(key=lambda r: (-r.oldest_days_overdue, -r.total_overdue_paise))
    return rows


def overdue_invoices(session: Session, company_id: int, as_of: date) -> list[InvoiceAgeing]:
    """Open invoices past their due date (days_overdue >= 1), sorted by days late."""
    invs = session.scalars(
        select(Invoice).where(Invoice.company_id == company_id, Invoice.balance_paise > 0, Invoice.due_date < as_of)
    ).all()
    return sorted(age_invoices(list(invs), as_of), key=lambda x: -x.days_overdue)
