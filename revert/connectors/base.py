"""Connector interface + the plain records every connector returns.

A connector is read-only: it pulls a Snapshot from an accounting system.
It never writes back.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


class ConnectorError(Exception):
    """The source answered, but with an error."""


class ConnectorUnavailable(ConnectorError):
    """The source could not be reached (e.g. Tally not running)."""


@dataclass
class CompanyInfo:
    name: str
    address: str = ""
    gstin: str = ""


@dataclass
class LedgerRecord:
    name: str
    parent: str = "Sundry Debtors"
    phones: list[str] = field(default_factory=list)
    email: str = ""
    address: str = ""
    credit_limit_paise: int | None = None
    credit_days: int | None = None
    closing_balance_paise: int = 0  # receivable (Dr) positive


@dataclass
class InvoiceLineRecord:
    item_name: str
    qty: float
    unit: str
    rate_paise: int
    amount_paise: int


@dataclass
class SalesInvoiceRecord:
    number: str
    date: date
    party_name: str
    total_paise: int
    lines: list[InvoiceLineRecord] = field(default_factory=list)
    subtotal_paise: int = 0
    tax_paise: int = 0
    narration: str = ""


@dataclass
class OutstandingBillRecord:
    number: str
    party_name: str
    bill_date: date
    due_date: date
    pending_paise: int


@dataclass
class AllocationRecord:
    invoice_number: str
    amount_paise: int


@dataclass
class ReceiptRecord:
    voucher_number: str
    date: date
    party_name: str
    amount_paise: int
    allocations: list[AllocationRecord] = field(default_factory=list)
    narration: str = ""


@dataclass
class StockItemRecord:
    name: str
    group: str
    unit: str
    qty: float
    rate_paise: int


@dataclass
class Snapshot:
    company: CompanyInfo
    ledgers: list[LedgerRecord]
    invoices: list[SalesInvoiceRecord]
    bills: list[OutstandingBillRecord]
    receipts: list[ReceiptRecord]
    stock: list[StockItemRecord]


class Connector(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_company(self) -> CompanyInfo: ...

    @abstractmethod
    def fetch_ledgers(self) -> list[LedgerRecord]: ...

    @abstractmethod
    def fetch_sales_invoices(self, from_date: date, to_date: date) -> list[SalesInvoiceRecord]: ...

    @abstractmethod
    def fetch_outstanding_bills(self) -> list[OutstandingBillRecord]: ...

    @abstractmethod
    def fetch_receipts(self, from_date: date, to_date: date) -> list[ReceiptRecord]: ...

    @abstractmethod
    def fetch_stock(self) -> list[StockItemRecord]: ...

    def fetch_snapshot(self, from_date: date, to_date: date) -> Snapshot:
        return Snapshot(
            company=self.fetch_company(),
            ledgers=self.fetch_ledgers(),
            invoices=self.fetch_sales_invoices(from_date, to_date),
            bills=self.fetch_outstanding_bills(),
            receipts=self.fetch_receipts(from_date, to_date),
            stock=self.fetch_stock(),
        )
