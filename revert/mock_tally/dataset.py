"""In-memory 'books' that the mock Tally server serves.

Mirrors how Tally derives things: a bill's pending amount is its invoice total
minus receipt allocations against it; a ledger's closing balance is the sum of
its pending bills (no opening balances in the demo).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import date

from revert.connectors.base import (
    AllocationRecord,
    CompanyInfo,
    LedgerRecord,
    OutstandingBillRecord,
    ReceiptRecord,
    SalesInvoiceRecord,
    StockItemRecord,
)


@dataclass
class TallyDataset:
    company: CompanyInfo
    ledgers: list[LedgerRecord]
    invoices: list[SalesInvoiceRecord]
    due_dates: dict[str, date]
    receipts: list[ReceiptRecord]
    stock: list[StockItemRecord]
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def pending_by_invoice(self) -> dict[str, int]:
        pending = {inv.number: inv.total_paise for inv in self.invoices}
        for r in self.receipts:
            for a in r.allocations:
                if a.invoice_number in pending:
                    pending[a.invoice_number] -= a.amount_paise
        return pending

    def outstanding_bills(self) -> list[OutstandingBillRecord]:
        pending = self.pending_by_invoice()
        return [
            OutstandingBillRecord(
                number=inv.number,
                party_name=inv.party_name,
                bill_date=inv.date,
                due_date=self.due_dates[inv.number],
                pending_paise=pending[inv.number],
            )
            for inv in self.invoices
            if pending[inv.number] > 0
        ]

    def ledgers_with_balances(self) -> list[LedgerRecord]:
        bal: dict[str, int] = {}
        for b in self.outstanding_bills():
            bal[b.party_name] = bal.get(b.party_name, 0) + b.pending_paise
        return [replace(l, closing_balance_paise=bal.get(l.name, 0)) for l in self.ledgers]

    def add_receipt(
        self,
        party_name: str,
        amount_paise: int,
        on: date,
        invoice_number: str | None = None,
        narration: str = "",
    ) -> ReceiptRecord:
        """Record a payment like an accountant would in Tally (demo helper).

        Allocates to `invoice_number` if given, else FIFO across the party's oldest pending bills.
        """
        if amount_paise <= 0:
            raise ValueError("amount must be positive")
        if party_name not in {l.name for l in self.ledgers}:
            raise ValueError(f"unknown party {party_name!r}")
        with self.lock:
            pending = self.pending_by_invoice()
            bills = sorted(
                (i for i in self.invoices if i.party_name == party_name and pending[i.number] > 0),
                key=lambda i: (i.date, i.number),
            )
            if invoice_number:
                bills = [b for b in bills if b.number == invoice_number]
                if not bills:
                    raise ValueError(f"{invoice_number} has nothing pending for {party_name}")
            allocations, left = [], amount_paise
            for inv in bills:
                if left <= 0:
                    break
                take = min(left, pending[inv.number])
                allocations.append(AllocationRecord(inv.number, take))
                left -= take
            if left > 0 and allocations:
                # Over-payment stays on the last bill as an advance would; keep it simple for the mock.
                allocations[-1].amount_paise += left
            number = f"RCT-{len(self.receipts) + 1:04d}"
            receipt = ReceiptRecord(number, on, party_name, amount_paise, allocations, narration)
            self.receipts.append(receipt)
            return receipt
