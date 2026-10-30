"""Render a TallyDataset as TallyPrime Collection-export XML (what real Tally sends back)."""
from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET

from revert.connectors.base import OutstandingBillRecord, ReceiptRecord, SalesInvoiceRecord
from revert.mock_tally.dataset import TallyDataset

BANK_LEDGER = "HDFC Bank A/c"


def _amt(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    r, p = divmod(abs(paise), 100)
    return f"{sign}{r}.{p:02d}"


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _qty(q: float) -> str:
    return f"{q:g}"


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def envelope(items: list[ET.Element]) -> str:
    env = ET.Element("ENVELOPE")
    header = _sub(env, "HEADER")
    _sub(header, "VERSION", "1")
    _sub(header, "STATUS", "1")
    body = _sub(env, "BODY")
    _sub(body, "DESC")
    coll = _sub(_sub(body, "DATA"), "COLLECTION")
    coll.extend(items)
    return ET.tostring(env, encoding="unicode")


def error_envelope(message: str) -> str:
    env = ET.Element("RESPONSE")
    _sub(env, "LINEERROR", message)
    return ET.tostring(env, encoding="unicode")


def render_company(ds: TallyDataset) -> str:
    c = ET.Element("COMPANY", NAME=ds.company.name)
    addr = _sub(c, "ADDRESS.LIST", TYPE="String")
    for part in ds.company.address.split(", "):
        _sub(addr, "ADDRESS", part, TYPE="String")
    _sub(c, "GSTREGISTRATIONNUMBER", ds.company.gstin, TYPE="String")
    return envelope([c])


def render_ledgers(ds: TallyDataset) -> str:
    items = []
    for l in ds.ledgers_with_balances():
        el = ET.Element("LEDGER", NAME=l.name, RESERVEDNAME="")
        _sub(el, "PARENT", l.parent, TYPE="String")
        addr = _sub(el, "ADDRESS.LIST", TYPE="String")
        _sub(addr, "ADDRESS", l.address, TYPE="String")
        if l.phones:
            _sub(el, "LEDGERMOBILE", l.phones[0], TYPE="String")
        if len(l.phones) > 1:
            _sub(el, "LEDGERPHONE", l.phones[1], TYPE="String")
        _sub(el, "EMAIL", l.email, TYPE="String")
        if l.credit_limit_paise is not None:
            _sub(el, "CREDITLIMIT", _amt(-l.credit_limit_paise), TYPE="Amount")
        if l.credit_days is not None:
            _sub(el, "BILLCREDITPERIOD", f"{l.credit_days} Days", TYPE="Due Date", P=f"{l.credit_days} Days")
        _sub(el, "CLOSINGBALANCE", _amt(-l.closing_balance_paise), TYPE="Amount")
        items.append(el)
    return envelope(items)


def _sales_voucher(inv: SalesInvoiceRecord, due: date) -> ET.Element:
    v = ET.Element("VOUCHER", VCHTYPE="Sales", ACTION="Create", OBJVIEW="Invoice Voucher View")
    _sub(v, "DATE", _d(inv.date), TYPE="Date")
    _sub(v, "VOUCHERTYPENAME", "Sales")
    _sub(v, "VOUCHERNUMBER", inv.number)
    _sub(v, "PARTYLEDGERNAME", inv.party_name)
    _sub(v, "NARRATION", inv.narration)
    for line in inv.lines:
        ie = _sub(v, "ALLINVENTORYENTRIES.LIST")
        _sub(ie, "STOCKITEMNAME", line.item_name)
        _sub(ie, "ISDEEMEDPOSITIVE", "No")
        _sub(ie, "RATE", f"{_amt(line.rate_paise)}/{line.unit}")
        _sub(ie, "ACTUALQTY", f" {_qty(line.qty)} {line.unit}")
        _sub(ie, "BILLEDQTY", f" {_qty(line.qty)} {line.unit}")
        _sub(ie, "AMOUNT", _amt(line.amount_paise))
    party = _sub(v, "LEDGERENTRIES.LIST")
    _sub(party, "LEDGERNAME", inv.party_name)
    _sub(party, "ISDEEMEDPOSITIVE", "Yes")
    _sub(party, "AMOUNT", _amt(-inv.total_paise))
    ba = _sub(party, "BILLALLOCATIONS.LIST")
    _sub(ba, "NAME", inv.number)
    _sub(ba, "BILLTYPE", "New Ref")
    days = (due - inv.date).days
    _sub(ba, "BILLCREDITPERIOD", f"{days} Days", P=f"{days} Days")
    _sub(ba, "AMOUNT", _amt(-inv.total_paise))
    half = inv.tax_paise // 2
    for name, amt in (("CGST Output 9%", half), ("SGST Output 9%", inv.tax_paise - half)):
        le = _sub(v, "LEDGERENTRIES.LIST")
        _sub(le, "LEDGERNAME", name)
        _sub(le, "ISDEEMEDPOSITIVE", "No")
        _sub(le, "AMOUNT", _amt(amt))
    round_off = inv.total_paise - inv.subtotal_paise - inv.tax_paise
    if round_off:
        le = _sub(v, "LEDGERENTRIES.LIST")
        _sub(le, "LEDGERNAME", "Round Off")
        _sub(le, "AMOUNT", _amt(round_off))
    return v


def render_sales(ds: TallyDataset, from_date: date | None, to_date: date | None) -> str:
    return envelope([
        _sales_voucher(inv, ds.due_dates[inv.number])
        for inv in ds.invoices
        if (from_date is None or inv.date >= from_date) and (to_date is None or inv.date <= to_date)
    ])


def _bill(b: OutstandingBillRecord) -> ET.Element:
    el = ET.Element("BILL", NAME=b.number)
    _sub(el, "PARENT", b.party_name, TYPE="String")
    _sub(el, "BILLDATE", _d(b.bill_date), TYPE="Date")
    days = (b.due_date - b.bill_date).days
    _sub(el, "BILLCREDITPERIOD", f"{days} Days", TYPE="Due Date", P=f"{days} Days")
    _sub(el, "CLOSINGBALANCE", _amt(-b.pending_paise), TYPE="Amount")
    return el


def render_bills(ds: TallyDataset) -> str:
    return envelope([_bill(b) for b in ds.outstanding_bills()])


def _receipt(r: ReceiptRecord) -> ET.Element:
    v = ET.Element("VOUCHER", VCHTYPE="Receipt", ACTION="Create")
    _sub(v, "DATE", _d(r.date), TYPE="Date")
    _sub(v, "VOUCHERTYPENAME", "Receipt")
    _sub(v, "VOUCHERNUMBER", r.voucher_number)
    _sub(v, "PARTYLEDGERNAME", r.party_name)
    _sub(v, "NARRATION", r.narration)
    party = _sub(v, "ALLLEDGERENTRIES.LIST")
    _sub(party, "LEDGERNAME", r.party_name)
    _sub(party, "ISDEEMEDPOSITIVE", "No")
    _sub(party, "AMOUNT", _amt(r.amount_paise))
    for a in r.allocations:
        ba = _sub(party, "BILLALLOCATIONS.LIST")
        _sub(ba, "NAME", a.invoice_number)
        _sub(ba, "BILLTYPE", "Agst Ref")
        _sub(ba, "AMOUNT", _amt(a.amount_paise))
    bank = _sub(v, "ALLLEDGERENTRIES.LIST")
    _sub(bank, "LEDGERNAME", BANK_LEDGER)
    _sub(bank, "ISDEEMEDPOSITIVE", "Yes")
    _sub(bank, "AMOUNT", _amt(-r.amount_paise))
    return v


def render_receipts(ds: TallyDataset, from_date: date | None, to_date: date | None) -> str:
    return envelope([
        _receipt(r)
        for r in ds.receipts
        if (from_date is None or r.date >= from_date) and (to_date is None or r.date <= to_date)
    ])


def render_stock(ds: TallyDataset, price_date: date) -> str:
    items = []
    for s in ds.stock:
        el = ET.Element("STOCKITEM", NAME=s.name, RESERVEDNAME="")
        _sub(el, "PARENT", s.group, TYPE="String")
        _sub(el, "BASEUNITS", s.unit, TYPE="String")
        _sub(el, "CLOSINGBALANCE", f" {_qty(s.qty)} {s.unit}", TYPE="Quantity")
        _sub(el, "CLOSINGRATE", f"{_amt(s.rate_paise)}/{s.unit}", TYPE="Rate")
        pl = _sub(el, "STANDARDPRICELIST.LIST")
        _sub(pl, "DATE", _d(price_date), TYPE="Date")
        _sub(pl, "RATE", f"{_amt(s.rate_paise)}/{s.unit}", TYPE="Rate")
        items.append(el)
    return envelope(items)
