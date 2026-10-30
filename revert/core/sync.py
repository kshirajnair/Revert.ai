"""Sync job: pull a Snapshot from the connector and upsert it into our DB.

- All-or-nothing: the snapshot is fetched completely before anything is written,
  and written in one transaction. If Tally is down, existing data (and
  last_synced_at) are left untouched and a failed SyncRun is recorded.
- Revert-owned buyer fields (tone, paused, language, manual phones) are never overwritten.
- Returns the invoice numbers whose balance dropped, so the reminder engine
  (Phase 2) can stop chasing them immediately.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from revert.connectors.base import Connector, ConnectorError, LedgerRecord, Snapshot
from revert.core.models import (
    Buyer,
    BuyerPhone,
    Company,
    Invoice,
    InvoiceLine,
    Receipt,
    ReceiptAllocation,
    StockItem,
    SyncRun,
)
from revert.core.phones import normalize_phone
from revert.core.timeutil import today_ist, utcnow

log = logging.getLogger(__name__)

SYNC_WINDOW_DAYS = 365


@dataclass
class SyncResult:
    ok: bool
    run_id: int
    company_id: int | None = None
    error: str = ""
    counts: dict = field(default_factory=dict)
    paid_invoices: list[str] = field(default_factory=list)


def run_sync(session: Session, connector: Connector, as_of: date | None = None) -> SyncResult:
    as_of = as_of or today_ist()
    run = SyncRun(source=connector.name, started_at=utcnow())
    session.add(run)
    session.commit()

    try:
        snap = connector.fetch_snapshot(as_of - timedelta(days=SYNC_WINDOW_DAYS), as_of)
    except ConnectorError as e:
        log.warning("sync failed: %s", e)
        run.status, run.error, run.finished_at = "failed", str(e), utcnow()
        session.commit()
        return SyncResult(ok=False, run_id=run.id, error=str(e))

    run_id = run.id
    try:
        company, counts, paid = _apply(session, snap, utcnow())
        run.company_id, run.status, run.counts, run.finished_at = company.id, "ok", counts, utcnow()
        session.commit()
    except Exception as e:
        session.rollback()
        run = session.get(SyncRun, run_id)
        run.status, run.error, run.finished_at = "failed", f"apply error: {e}", utcnow()
        session.commit()
        raise
    return SyncResult(ok=True, run_id=run_id, company_id=company.id, counts=counts, paid_invoices=paid)


def _sync_phones(buyer: Buyer, ledger: LedgerRecord) -> None:
    wanted = [p for p in (normalize_phone(x) for x in ledger.phones) if p]
    for ph in list(buyer.phones):
        if ph.source == "tally" and ph.phone not in wanted:
            buyer.phones.remove(ph)
    have = {ph.phone for ph in buyer.phones}
    for p in dict.fromkeys(wanted):
        if p not in have:
            buyer.phones.append(BuyerPhone(phone=p, source="tally"))
    if buyer.phones and not any(ph.is_primary for ph in buyer.phones):
        buyer.phones[0].is_primary = True


def _apply(session: Session, snap: Snapshot, now: datetime) -> tuple[Company, dict, list[str]]:
    company = session.scalar(select(Company).where(Company.name == snap.company.name))
    if company is None:
        company = Company(name=snap.company.name)
        session.add(company)
    company.address = snap.company.address
    company.gstin = snap.company.gstin
    session.flush()

    buyers = {b.ledger_name: b for b in session.scalars(select(Buyer).where(Buyer.company_id == company.id))}
    for l in snap.ledgers:
        b = buyers.get(l.name)
        if b is None:
            b = Buyer(company_id=company.id, ledger_name=l.name)
            session.add(b)
            buyers[l.name] = b
        b.address, b.email = l.address, l.email
        b.credit_limit_paise, b.credit_days = l.credit_limit_paise, l.credit_days
        b.closing_balance_paise = l.closing_balance_paise
        b.synced_at = now
        _sync_phones(b, l)
    session.flush()

    bills = {b.number: b for b in snap.bills}
    invoices = {i.number: i for i in session.scalars(select(Invoice).where(Invoice.company_id == company.id))}
    paid, skipped = [], 0
    for rec in snap.invoices:
        buyer = buyers.get(rec.party_name)
        if buyer is None:
            skipped += 1
            log.warning("invoice %s: party %r is not a debtor ledger, skipped", rec.number, rec.party_name)
            continue
        bill = bills.get(rec.number)
        balance = bill.pending_paise if bill else 0
        row = invoices.get(rec.number)
        if row is None:
            row = Invoice(company_id=company.id, number=rec.number)
            session.add(row)
            invoices[rec.number] = row
        elif balance < row.balance_paise:
            paid.append(rec.number)
        if bill:
            row.due_date = bill.due_date
        elif row.due_date is None:
            row.due_date = rec.date + timedelta(days=buyer.credit_days or 0)
        row.buyer_id = buyer.id
        row.date = rec.date
        row.subtotal_paise, row.tax_paise, row.total_paise = rec.subtotal_paise, rec.tax_paise, rec.total_paise
        row.balance_paise = balance
        row.narration = rec.narration
        row.synced_at = now
        row.lines = [
            InvoiceLine(item_name=l.item_name, qty=l.qty, unit=l.unit, rate_paise=l.rate_paise, amount_paise=l.amount_paise)
            for l in rec.lines
        ]

    receipts = {r.voucher_number: r for r in session.scalars(select(Receipt).where(Receipt.company_id == company.id))}
    for rec in snap.receipts:
        buyer = buyers.get(rec.party_name)
        if buyer is None:
            skipped += 1
            continue
        row = receipts.get(rec.voucher_number)
        if row is None:
            row = Receipt(company_id=company.id, voucher_number=rec.voucher_number)
            session.add(row)
        row.buyer_id, row.date, row.amount_paise, row.narration = buyer.id, rec.date, rec.amount_paise, rec.narration
        row.synced_at = now
        row.allocations = [ReceiptAllocation(invoice_number=a.invoice_number, amount_paise=a.amount_paise)
                           for a in rec.allocations]

    stock = {s.name: s for s in session.scalars(select(StockItem).where(StockItem.company_id == company.id))}
    for rec in snap.stock:
        row = stock.get(rec.name)
        if row is None:
            row = StockItem(company_id=company.id, name=rec.name)
            session.add(row)
        row.group, row.unit, row.qty, row.rate_paise, row.synced_at = rec.group, rec.unit, rec.qty, rec.rate_paise, now

    company.last_synced_at = now
    counts = {
        "ledgers": len(snap.ledgers),
        "invoices": len(snap.invoices),
        "outstanding_bills": len(snap.bills),
        "receipts": len(snap.receipts),
        "stock_items": len(snap.stock),
        "skipped": skipped,
        "paid_this_sync": len(paid),
    }
    return company, counts, paid
