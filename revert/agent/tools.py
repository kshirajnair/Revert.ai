"""The agent's tools: the only way it reads or changes data.

Read tools return DB rows and record, in `Facts`, the exact text of every number they expose
(rendered with the same formatters the replies use). The guard then checks that every number
in a reply is one of these facts or was written by the dealer. That is how "never invent a
number" is enforced in code rather than by prompt.

Every query is scoped to the buyer who sent the message: a dealer can never see another
dealer's invoices or payments, even by typing their invoice number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from revert.agent.parse import Installment
from revert.core.models import (
    Buyer,
    Company,
    Dispute,
    Invoice,
    MessageLog,
    PaymentClaim,
    PaymentPromise,
    Receipt,
    StockItem,
)
from revert.core.money import format_inr_exact
from revert.core.timeutil import format_ist, utcnow

CLAIM_HOLD_DAYS = 3


def money(paise: int) -> str:
    return format_inr_exact(paise)


def day(d: date) -> str:
    return f"{d:%d %b %Y}"


def qty_text(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


@dataclass
class Facts:
    """Text of every value the tools handed out during one reply."""

    texts: list[str] = field(default_factory=list)

    def add(self, *values: object) -> None:
        self.texts.extend(str(v) for v in values if v is not None and v != "")


@dataclass
class Ctx:
    session: Session
    company: Company
    buyer: Buyer
    today: date
    facts: Facts = field(default_factory=Facts)
    now_utc: datetime | None = None
    undo: list = field(default_factory=list)  # callables that reverse this turn's writes

    def rollback(self) -> None:
        """Reverse the writes of this turn only (the inbound message log stays)."""
        for fn in reversed(self.undo):
            fn()
        self.undo.clear()
        self.session.flush()

    def added(self, obj) -> None:
        self.session.add(obj)
        self.undo.append(lambda: self.session.delete(obj))

    def changed(self, obj, attr: str, value) -> None:
        old = getattr(obj, attr)
        setattr(obj, attr, value)
        self.undo.append(lambda: setattr(obj, attr, old))


# --- read tools -----------------------------------------------------------------------------

def synced_at(ctx: Ctx) -> str:
    text = format_ist(ctx.company.last_synced_at)
    ctx.facts.add(text)
    return text


@dataclass
class Dues:
    invoices: list[Invoice]
    total_paise: int


def open_dues(ctx: Ctx) -> Dues:
    invs = list(ctx.session.scalars(
        select(Invoice).where(Invoice.buyer_id == ctx.buyer.id, Invoice.balance_paise > 0)
        .order_by(Invoice.due_date, Invoice.number)))
    total = sum(i.balance_paise for i in invs)
    for i in invs:
        ctx.facts.add(i.number, day(i.date), day(i.due_date), money(i.balance_paise))
    ctx.facts.add(money(total))
    return Dues(invs, total)


def recent_receipts(ctx: Ctx, days: int, limit: int = 5) -> list[Receipt]:
    rows = list(ctx.session.scalars(
        select(Receipt).where(Receipt.buyer_id == ctx.buyer.id, Receipt.date >= ctx.today - timedelta(days=days))
        .order_by(Receipt.date.desc(), Receipt.id.desc()).limit(limit)))
    _note_receipts(ctx, rows)
    return rows


def last_receipts(ctx: Ctx, limit: int = 3) -> list[Receipt]:
    rows = list(ctx.session.scalars(
        select(Receipt).where(Receipt.buyer_id == ctx.buyer.id).order_by(Receipt.date.desc(), Receipt.id.desc())
        .limit(limit)))
    _note_receipts(ctx, rows)
    return rows


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def receipt_by_reference(ctx: Ctx, reference: str) -> Receipt | None:
    """A receipt of THIS buyer whose narration carries the UTR / reference."""
    ref = _squash(reference)
    if len(ref) < 6:
        return None
    for r in ctx.session.scalars(select(Receipt).where(Receipt.buyer_id == ctx.buyer.id)):
        if ref in _squash(r.narration) or ref == _squash(r.voucher_number):
            _note_receipts(ctx, [r])
            return r
    return None


def receipt_ref(r: Receipt) -> str:
    return r.narration or r.voucher_number


def _note_receipts(ctx: Ctx, rows: list[Receipt]) -> None:
    for r in rows:
        ctx.facts.add(money(r.amount_paise), day(r.date), r.voucher_number, receipt_ref(r))


def find_invoices(ctx: Ctx, refs: list[str] | tuple[str, ...]) -> list[Invoice]:
    """This buyer's invoices whose number has the given digits ('1043' matches 'INV-1043')."""
    wanted = {r.lstrip("0") for r in refs if r}
    if not wanted:
        return []
    invs = ctx.session.scalars(
        select(Invoice).where(Invoice.buyer_id == ctx.buyer.id).options(selectinload(Invoice.lines))).all()
    found = [i for i in invs if re.sub(r"\D", "", i.number).lstrip("0") in wanted]
    for i in found:
        _note_invoice(ctx, i)
    return sorted(found, key=lambda i: i.number)


def latest_invoice(ctx: Ctx) -> Invoice | None:
    inv = ctx.session.scalar(select(Invoice).where(Invoice.buyer_id == ctx.buyer.id)
                             .options(selectinload(Invoice.lines)).order_by(Invoice.date.desc(), Invoice.id.desc()))
    if inv:
        _note_invoice(ctx, inv)
    return inv


def _note_invoice(ctx: Ctx, i: Invoice) -> None:
    ctx.facts.add(i.number, day(i.date), day(i.due_date), money(i.subtotal_paise), money(i.tax_paise),
                  money(i.total_paise), money(i.paid_paise), money(i.balance_paise))
    for line in i.lines:
        ctx.facts.add(line.item_name, qty_text(line.qty), line.unit, money(line.rate_paise), money(line.amount_paise))


_CATEGORY = {"mcb": "mcb", "mcbs": "mcb", "rccb": "rccb", "rccbs": "rccb", "isolator": "isolator", "db": "board",
             "board": "board", "boards": "board", "switch": "switch", "switches": "switch", "socket": "socket",
             "sockets": "socket", "plate": "plate", "plates": "plate", "wire": "wire", "wires": "wire",
             "cable": "wire", "cables": "wire", "coil": "wire", "changeover": "changeover"}
_SYNONYMS = {"single": "sp", "double": "dp", "triple": "tp", "four": "4p", "distribution": "board"}


def _tokens(text: str) -> set[str]:
    s = text.lower()
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(?:amps?|a)\b", r"\1a", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*mm|sqmm|mm2?)\b", r"\1sqmm", s)
    out = set()
    for t in re.findall(r"\d+(?:\.\d+)?[a-z]*|[a-z]+", s):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", t)
        if m:
            t = f"{float(m.group(1)):g}{m.group(2)}"
        out.add(_SYNONYMS.get(t, t))
    return out


def find_stock(ctx: Ctx, query: str, limit: int = 6) -> list[StockItem]:
    q = _tokens(query)
    cats = {_CATEGORY[t] for t in q if t in _CATEGORY}
    if not cats:
        return []
    items = ctx.session.scalars(select(StockItem).where(StockItem.company_id == ctx.company.id)).all()
    scored = []
    for it in items:
        toks = _tokens(f"{it.name} {it.group}")
        if not {_CATEGORY.get(t) for t in toks} & cats:
            continue
        scored.append((it, toks))
    # spec tokens like "16a" or "2.5sqmm" must match when some candidate has them
    for t in [t for t in q if t[0].isdigit()]:
        if any(t in toks for _, toks in scored):
            scored = [(it, toks) for it, toks in scored if t in toks]
    if not scored:
        return []
    best = max(len(q & toks) for _, toks in scored)
    found = [it for it, toks in scored if len(q & toks) == best][:limit]
    for it in found:
        ctx.facts.add(it.name, qty_text(it.qty), it.unit, money(it.rate_paise))
    return found


# --- write tools ----------------------------------------------------------------------------

def log_promise(ctx: Ctx, installments: list[Installment], dues_total_paise: int,
                message: MessageLog) -> list[PaymentPromise]:
    """Record a promise; older open promises from this buyer are superseded."""
    for old in ctx.session.scalars(select(PaymentPromise).where(PaymentPromise.buyer_id == ctx.buyer.id,
                                                                PaymentPromise.status == "open")):
        ctx.changed(old, "status", "superseded")
    rows, running = [], 0
    for inst in sorted(installments, key=lambda i: i.due):
        if inst.amount_paise is not None:
            kind, amount = "amount", inst.amount_paise
        elif inst.rest and running:
            kind, amount = "rest", max(dues_total_paise - running, 0)
        else:
            kind, amount = "full", max(dues_total_paise - running, 0)
        if amount <= 0:
            continue
        running += amount
        p = PaymentPromise(company_id=ctx.company.id, buyer_id=ctx.buyer.id, message_id=message.id, kind=kind,
                           amount_paise=amount, cumulative_paise=running, due_date=inst.due, promised_on=ctx.today,
                           created_at=ctx.now_utc or utcnow())
        ctx.added(p)
        ctx.facts.add(money(amount), day(inst.due))
        rows.append(p)
    ctx.session.flush()
    return rows


def log_dispute(ctx: Ctx, invoice: Invoice | None, reason: str, message: MessageLog) -> Dispute:
    d = Dispute(company_id=ctx.company.id, buyer_id=ctx.buyer.id, invoice_id=invoice.id if invoice else None,
                message_id=message.id, reason=reason[:1000], created_at=ctx.now_utc or utcnow())
    ctx.added(d)
    ctx.session.flush()
    return d


def log_claim(ctx: Ctx, reference: str, message: MessageLog) -> PaymentClaim:
    c = PaymentClaim(company_id=ctx.company.id, buyer_id=ctx.buyer.id, message_id=message.id,
                     reference=reference[:40], created_at=ctx.now_utc or utcnow())
    ctx.added(c)
    ctx.session.flush()
    return c


def close_claims_matched_by(ctx: Ctx, receipt: Receipt) -> None:
    for c in ctx.session.scalars(select(PaymentClaim).where(PaymentClaim.buyer_id == ctx.buyer.id,
                                                            PaymentClaim.status == "open")):
        if not c.reference or _squash(c.reference) in _squash(receipt.narration):
            ctx.changed(c, "status", "matched")


def pause_reminders(ctx: Ctx) -> None:
    ctx.changed(ctx.buyer, "paused", True)


def match_claims(session: Session, company_id: int) -> int:
    """After a sync: claims whose reference now shows up in a receipt are matched."""
    n = 0
    claims = session.scalars(select(PaymentClaim).where(PaymentClaim.company_id == company_id,
                                                        PaymentClaim.status == "open",
                                                        PaymentClaim.reference != "")).all()
    for c in claims:
        ref = _squash(c.reference)
        if any(ref in _squash(r.narration) for r in session.scalars(select(Receipt).where(Receipt.buyer_id == c.buyer_id))):
            c.status = "matched"
            n += 1
    session.commit()
    return n


def holds_for(session: Session, company_id: int, as_of: date, now_utc: datetime) -> dict:
    """Why reminders should wait, from the agent's records:
    {'promise': {buyer_id: due_date}, 'claim': {buyer_id}, 'dispute_buyers': {buyer_id},
     'dispute_invoices': {invoice_id}}"""
    promise = dict(session.execute(
        select(PaymentPromise.buyer_id, func.max(PaymentPromise.due_date))
        .where(PaymentPromise.company_id == company_id, PaymentPromise.status == "open",
               PaymentPromise.due_date >= as_of).group_by(PaymentPromise.buyer_id)).all())
    claim = set(session.scalars(select(PaymentClaim.buyer_id).where(
        PaymentClaim.company_id == company_id, PaymentClaim.status == "open",
        PaymentClaim.created_at > now_utc - timedelta(days=CLAIM_HOLD_DAYS))))
    disputes = session.execute(select(Dispute.buyer_id, Dispute.invoice_id).where(
        Dispute.company_id == company_id, Dispute.status == "open")).all()
    return {"promise": promise, "claim": claim,
            "dispute_buyers": {b for b, i in disputes if i is None},
            "dispute_invoices": {i for _, i in disputes if i is not None}}
