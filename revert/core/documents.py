"""PDF attachments the agent can send: an invoice copy or a statement, built from the DB."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from revert.core.models import Buyer, Invoice, Receipt
from revert.core.pdf import invoice_pdf, statement_pdf
from revert.core.timeutil import format_ist


def build(session: Session, buyer: Buyer, attachment: dict, today: date) -> tuple[bytes, str]:
    """attachment: {"kind": "invoice", "invoice_id": n} or {"kind": "statement"}. Returns (pdf, filename)."""
    company = buyer.company
    synced = format_ist(company.last_synced_at)
    if attachment["kind"] == "invoice":
        inv = session.get(Invoice, attachment["invoice_id"])
        if inv is None or inv.buyer_id != buyer.id:  # never another dealer's invoice
            raise LookupError("invoice not found for this buyer")
        return invoice_pdf(company, buyer, inv, synced), f"{inv.number}.pdf"
    if attachment["kind"] == "statement":
        open_invoices = list(session.scalars(select(Invoice).where(Invoice.buyer_id == buyer.id,
                                                                   Invoice.balance_paise > 0)
                                             .order_by(Invoice.due_date, Invoice.number)))
        receipts = list(session.scalars(select(Receipt).where(Receipt.buyer_id == buyer.id)
                                        .order_by(Receipt.date.desc(), Receipt.id.desc()).limit(5)))
        return (statement_pdf(company, buyer, open_invoices, receipts, today, synced),
                f"Statement-{today:%Y-%m-%d}.pdf")
    raise ValueError(f"unknown attachment kind {attachment['kind']!r}")
