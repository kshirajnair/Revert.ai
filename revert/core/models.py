"""SQLAlchemy models.

Conventions:
- money: integer paise (`*_paise` columns)
- timestamps: naive UTC; business dates (invoice/due/receipt): plain DATE in IST
- fields synced from the accounting system are overwritten on every sync;
  Revert-owned fields (tone, paused, language) are never touched by sync.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from revert.core.timeutil import utcnow

TONES = ("gentle", "standard", "firm")
LANGUAGES = ("en", "hi", "kn")
# Reminder stages, in days relative to the due date: 3 before, on the day, +7, +15, +30.
DEFAULT_REMINDER_OFFSETS = [-3, 0, 7, 15, 30]


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    address: Mapped[str] = mapped_column(String(500), default="")
    gstin: Mapped[str] = mapped_column(String(20), default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    buyers: Mapped[list[Buyer]] = relationship(back_populates="company")


class Buyer(Base):
    """A customer ledger (Sundry Debtor in Tally)."""

    __tablename__ = "buyers"
    __table_args__ = (UniqueConstraint("company_id", "ledger_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    # synced
    ledger_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    credit_limit_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closing_balance_paise: Mapped[int] = mapped_column(Integer, default=0)  # receivable, Dr positive
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Revert-owned
    tone: Mapped[str] = mapped_column(String(10), default="standard")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(5), default="en")

    company: Mapped[Company] = relationship(back_populates="buyers")
    phones: Mapped[list[BuyerPhone]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    invoices: Mapped[list[Invoice]] = relationship(back_populates="buyer")
    receipts: Mapped[list[Receipt]] = relationship(back_populates="buyer")

    @property
    def primary_phone(self) -> str | None:
        for p in self.phones:
            if p.is_primary:
                return p.phone
        return self.phones[0].phone if self.phones else None


class BuyerPhone(Base):
    """The ONLY numbers Revert may message for a buyer."""

    __tablename__ = "buyer_phones"
    __table_args__ = (UniqueConstraint("buyer_id", "phone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"))
    phone: Mapped[str] = mapped_column(String(20), index=True)  # E.164
    source: Mapped[str] = mapped_column(String(10), default="tally")  # tally | manual
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    buyer: Mapped[Buyer] = relationship(back_populates="phones")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("company_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    number: Mapped[str] = mapped_column(String(50))
    date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    subtotal_paise: Mapped[int] = mapped_column(Integer, default=0)
    tax_paise: Mapped[int] = mapped_column(Integer, default=0)
    total_paise: Mapped[int] = mapped_column(Integer)
    balance_paise: Mapped[int] = mapped_column(Integer)  # pending, from bill-wise outstanding
    narration: Mapped[str] = mapped_column(Text, default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    buyer: Mapped[Buyer] = relationship(back_populates="invoices")
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    @property
    def paid_paise(self) -> int:
        return self.total_paise - self.balance_paise

    @property
    def status(self) -> str:
        if self.balance_paise <= 0:
            return "paid"
        if self.balance_paise < self.total_paise:
            return "part_paid"
        return "unpaid"


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    item_name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")
    rate_paise: Mapped[int] = mapped_column(Integer)
    amount_paise: Mapped[int] = mapped_column(Integer)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("company_id", "voucher_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    voucher_number: Mapped[str] = mapped_column(String(50))
    date: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(Integer)
    narration: Mapped[str] = mapped_column(Text, default="")  # often carries UTR
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    buyer: Mapped[Buyer] = relationship(back_populates="receipts")
    allocations: Mapped[list[ReceiptAllocation]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class ReceiptAllocation(Base):
    __tablename__ = "receipt_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    invoice_number: Mapped[str] = mapped_column(String(50), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)

    receipt: Mapped[Receipt] = relationship(back_populates="allocations")


class StockItem(Base):
    __tablename__ = "stock_items"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(100), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    qty: Mapped[float] = mapped_column(Float, default=0)
    rate_paise: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="running")  # running | ok | failed
    error: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)


class MessageLog(Base):
    """Every inbound/outbound message. `outcome` seeds the future learning engine."""

    __tablename__ = "message_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # null only for inbound messages from numbers we don't know
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("buyers.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # web_simulator | whatsapp
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    phone: Mapped[str] = mapped_column(String(20), default="")
    body: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(40), default="")
    action: Mapped[str] = mapped_column(String(80), default="")
    outcome: Mapped[str] = mapped_column(String(80), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    buyer: Mapped[Buyer | None] = relationship()


class ReminderPolicy(Base):
    """The owner's reminder schedule. One row per company, created with defaults on first use."""

    __tablename__ = "reminder_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    offsets: Mapped[list] = mapped_column(JSON, default=lambda: list(DEFAULT_REMINDER_OFFSETS))
    quiet_start_hour: Mapped[int] = mapped_column(Integer, default=20)  # IST, no messages from here...
    quiet_end_hour: Mapped[int] = mapped_column(Integer, default=9)  # ...until here
    max_per_week: Mapped[int] = mapped_column(Integer, default=2)  # per buyer, rolling 7 days


class Reminder(Base):
    """One row per (invoice, stage) that was acted on, so a stage never fires twice.

    status: sent | cancelled (a payment synced before the stage went out)
    """

    __tablename__ = "reminders"
    __table_args__ = (UniqueConstraint("invoice_id", "stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    stage: Mapped[int] = mapped_column(Integer)  # days relative to due date
    status: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(80), default="")
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message_log.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    invoice: Mapped[Invoice] = relationship()
    buyer: Mapped[Buyer] = relationship()


class PaymentPromise(Base):
    """One installment of a promise-to-pay ("50k now, rest on 15th" is two rows in one group).

    kind: amount (dealer named a figure) | full (whole pending) | rest (whatever remains).
    The promise is kept once receipts dated on/after `promised_on` reach `cumulative_paise`.
    status: open | kept | broken | superseded
    """

    __tablename__ = "payment_promises"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message_log.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))
    amount_paise: Mapped[int] = mapped_column(Integer)
    cumulative_paise: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    promised_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="open")
    followed_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    buyer: Mapped[Buyer] = relationship()


class Dispute(Base):
    """A dealer's complaint (wrong rate, short delivery...). Reminders stop for the invoice
    (or for the whole buyer when no invoice was named) until the owner resolves it."""

    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message_log.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    buyer: Mapped[Buyer] = relationship()
    invoice: Mapped[Invoice | None] = relationship()


class PaymentClaim(Base):
    """"I already paid" that the books don't show yet. Holds reminders for a few days while the
    owner checks; matched automatically when a synced receipt carries the same reference."""

    __tablename__ = "payment_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message_log.id"), nullable=True)
    reference: Mapped[str] = mapped_column(String(40), default="")  # UTR / UPI ref, if given
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | matched | closed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    buyer: Mapped[Buyer] = relationship()
