"""TallyPrime XML-over-HTTP connector. READ-ONLY: only ever sends Export requests.

Uses TDL "Collection" exports, which return flat, predictable XML:

    <ENVELOPE><BODY><DATA><COLLECTION>
        <LEDGER NAME="..."> <PARENT>Sundry Debtors</PARENT> ... </LEDGER>
    </COLLECTION></DATA></BODY></ENVELOPE>

Tally sign convention: debit amounts are negative in XML. A receivable (Dr)
balance of ₹45,000 arrives as -45000.00; we flip it so receivables are positive.

The mock server (revert.mock_tally) answers the same requests in the same format,
so switching between mock and real Tally is only a TALLY_URL change.
Field names are verified against real TallyPrime in Phase 5.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

import httpx

from revert.connectors.base import (
    AllocationRecord,
    CompanyInfo,
    Connector,
    ConnectorError,
    ConnectorUnavailable,
    InvoiceLineRecord,
    LedgerRecord,
    OutstandingBillRecord,
    ReceiptRecord,
    SalesInvoiceRecord,
    StockItemRecord,
)
from revert.core.money import rupees_to_paise

Transport = Callable[[str], str]

# Collection ids. The mock dispatches on TYPE + filters, not on these names.
C_COMPANY = "RevertCompany"
C_LEDGERS = "RevertDebtors"
C_SALES = "RevertSalesVouchers"
C_BILLS = "RevertReceivableBills"
C_RECEIPTS = "RevertReceiptVouchers"
C_STOCK = "RevertStockItems"

F_IS_SALES = "RevertIsSales"
F_IS_RECEIPT = "RevertIsReceipt"
F_IS_RECEIVABLE = "RevertIsReceivable"


# --------------------------------------------------------------------- requests

def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def tally_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def build_collection_request(
    collection_id: str,
    object_type: str,
    fetch: list[str],
    company: str = "",
    child_of: str | None = None,
    filters: dict[str, str] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> str:
    env = ET.Element("ENVELOPE")
    header = _sub(env, "HEADER")
    _sub(header, "VERSION", "1")
    _sub(header, "TALLYREQUEST", "Export")
    _sub(header, "TYPE", "Collection")
    _sub(header, "ID", collection_id)
    desc = _sub(_sub(env, "BODY"), "DESC")
    sv = _sub(desc, "STATICVARIABLES")
    _sub(sv, "SVEXPORTFORMAT", "$$SysName:XML")
    if company:
        _sub(sv, "SVCURRENTCOMPANY", company)
    if from_date:
        _sub(sv, "SVFROMDATE", tally_date(from_date), TYPE="Date")
    if to_date:
        _sub(sv, "SVTODATE", tally_date(to_date), TYPE="Date")
    msg = _sub(_sub(desc, "TDL"), "TDLMESSAGE")
    coll = _sub(msg, "COLLECTION", NAME=collection_id, ISMODIFY="No")
    _sub(coll, "TYPE", object_type)
    if child_of:
        _sub(coll, "CHILDOF", child_of)
        _sub(coll, "BELONGSTO", "Yes")
    _sub(coll, "FETCH", ", ".join(fetch))
    if filters:
        _sub(coll, "FILTER", ", ".join(filters))
        for name, formula in filters.items():
            _sub(msg, "SYSTEM", formula, TYPE="Formulae", NAME=name)
    return ET.tostring(env, encoding="unicode")


# ---------------------------------------------------------------- parse helpers

_BAD_CHARREF = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_xml(raw: str) -> str:
    """Tally happily emits invalid XML chars (e.g. &#4;). Strip them before parsing."""

    def fix(m: re.Match) -> str:
        v = m.group(1)
        code = int(v[1:], 16) if v[0] in "xX" else int(v)
        return m.group(0) if code in (9, 10, 13) or code >= 32 else ""

    return _CTRL.sub("", _BAD_CHARREF.sub(fix, raw))


def parse_envelope(raw: str) -> ET.Element:
    try:
        root = ET.fromstring(sanitize_xml(raw).strip())
    except ET.ParseError as e:
        raise ConnectorError(f"Tally returned malformed XML: {e}") from e
    err = root.find(".//LINEERROR")
    if err is not None:
        raise ConnectorError(f"Tally error: {(err.text or '').strip()}")
    return root


def _t(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _name(el: ET.Element) -> str:
    return (el.get("NAME") or _t(el, "NAME")).strip()


def parse_amount(text: str) -> int:
    """'-45,000.00' -> -4500000 paise. Tally sometimes suffixes ' Dr'/' Cr'."""
    s = text.strip()
    if not s:
        return 0
    sign = 1
    if s.endswith(" Dr"):
        s, sign = s[:-3], -1
    elif s.endswith(" Cr"):
        s = s[:-3]
    return sign * rupees_to_paise(s)


def parse_tally_date(text: str) -> date:
    s = text.strip()
    for fmt in ("%Y%m%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ConnectorError(f"Unrecognised Tally date {text!r}")


def parse_days(text: str) -> int | None:
    m = re.match(r"\s*(\d+)\s*Days?", text or "", re.I)
    return int(m.group(1)) if m else None


def parse_qty(text: str) -> tuple[float, str]:
    """' 420 Nos' -> (420.0, 'Nos')."""
    m = re.match(r"\s*(-?[\d,]*\.?\d+)\s*(.*)", text or "")
    if not m:
        return 0.0, ""
    return float(m.group(1).replace(",", "")), m.group(2).strip()


def parse_rate(text: str) -> int:
    """'1180.00/Nos' -> 118000 paise."""
    return rupees_to_paise((text or "0").split("/")[0]) if text else 0


def _due_date(bill_date: date, credit_el: ET.Element | None, fallback_days: int | None) -> date:
    if credit_el is not None:
        text = (credit_el.text or "").strip()
        if re.fullmatch(r"\d{8}", text):
            return parse_tally_date(text)
        days = parse_days(text) or parse_days(credit_el.get("P", ""))
        if days is not None:
            return bill_date + timedelta(days=days)
    return bill_date + timedelta(days=fallback_days or 0)


# ---------------------------------------------------------------------- parsers

def parse_company(raw: str) -> CompanyInfo:
    root = parse_envelope(raw)
    el = root.find(".//COLLECTION/COMPANY")
    if el is None:
        raise ConnectorError("No company loaded in Tally")
    address = ", ".join((a.text or "").strip() for a in el.findall("ADDRESS.LIST/ADDRESS") if a.text)
    return CompanyInfo(name=_name(el), address=address, gstin=_t(el, "GSTREGISTRATIONNUMBER"))


def parse_ledgers(raw: str) -> list[LedgerRecord]:
    out = []
    for el in parse_envelope(raw).iterfind(".//COLLECTION/LEDGER"):
        phones = [p for p in (_t(el, "LEDGERMOBILE"), _t(el, "LEDGERPHONE")) if p]
        limit = _t(el, "CREDITLIMIT")
        credit = el.find("BILLCREDITPERIOD")
        out.append(LedgerRecord(
            name=_name(el),
            parent=_t(el, "PARENT"),
            phones=phones,
            email=_t(el, "EMAIL"),
            address=", ".join((a.text or "").strip() for a in el.findall("ADDRESS.LIST/ADDRESS") if a.text),
            credit_limit_paise=abs(parse_amount(limit)) if limit else None,
            credit_days=parse_days(credit.text or "") if credit is not None else None,
            closing_balance_paise=-parse_amount(_t(el, "CLOSINGBALANCE")),
        ))
    return out


def _ledger_entries(voucher: ET.Element) -> list[ET.Element]:
    # Invoice-mode vouchers use LEDGERENTRIES.LIST; voucher-mode uses ALLLEDGERENTRIES.LIST.
    return voucher.findall("ALLLEDGERENTRIES.LIST") + voucher.findall("LEDGERENTRIES.LIST")


def parse_sales_vouchers(raw: str) -> list[SalesInvoiceRecord]:
    out = []
    for v in parse_envelope(raw).iterfind(".//COLLECTION/VOUCHER"):
        party = _t(v, "PARTYLEDGERNAME")
        lines = []
        for ie in v.findall("ALLINVENTORYENTRIES.LIST") + v.findall("INVENTORYENTRIES.LIST"):
            qty, unit = parse_qty(_t(ie, "BILLEDQTY") or _t(ie, "ACTUALQTY"))
            lines.append(InvoiceLineRecord(
                item_name=_t(ie, "STOCKITEMNAME"),
                qty=qty,
                unit=unit,
                rate_paise=parse_rate(_t(ie, "RATE")),
                amount_paise=abs(parse_amount(_t(ie, "AMOUNT"))),
            ))
        total = tax = 0
        for le in _ledger_entries(v):
            name, amt = _t(le, "LEDGERNAME"), parse_amount(_t(le, "AMOUNT"))
            if name == party:
                total = -amt  # party is debited
            elif "GST" in name.upper():
                tax += amt
        out.append(SalesInvoiceRecord(
            number=_t(v, "VOUCHERNUMBER"),
            date=parse_tally_date(_t(v, "DATE")),
            party_name=party,
            total_paise=total,
            lines=lines,
            subtotal_paise=sum(l.amount_paise for l in lines),
            tax_paise=tax,
            narration=_t(v, "NARRATION"),
        ))
    return out


def parse_bills(raw: str, credit_days: dict[str, int | None] | None = None) -> list[OutstandingBillRecord]:
    credit_days = credit_days or {}
    out = []
    for el in parse_envelope(raw).iterfind(".//COLLECTION/BILL"):
        party = _t(el, "PARENT")
        bill_date = parse_tally_date(_t(el, "BILLDATE"))
        pending = -parse_amount(_t(el, "CLOSINGBALANCE"))
        if pending <= 0:
            continue  # settled or advance
        out.append(OutstandingBillRecord(
            number=_name(el),
            party_name=party,
            bill_date=bill_date,
            due_date=_due_date(bill_date, el.find("BILLCREDITPERIOD"), credit_days.get(party)),
            pending_paise=pending,
        ))
    return out


def parse_receipts(raw: str) -> list[ReceiptRecord]:
    out = []
    for v in parse_envelope(raw).iterfind(".//COLLECTION/VOUCHER"):
        party = _t(v, "PARTYLEDGERNAME")
        amount, allocations = 0, []
        for le in _ledger_entries(v):
            if _t(le, "LEDGERNAME") != party:
                continue
            amount += parse_amount(_t(le, "AMOUNT"))  # party is credited -> positive
            for ba in le.findall("BILLALLOCATIONS.LIST"):
                if _t(ba, "BILLTYPE") in ("Agst Ref", "New Ref", "") and _t(ba, "NAME"):
                    allocations.append(AllocationRecord(_t(ba, "NAME"), parse_amount(_t(ba, "AMOUNT"))))
        out.append(ReceiptRecord(
            voucher_number=_t(v, "VOUCHERNUMBER"),
            date=parse_tally_date(_t(v, "DATE")),
            party_name=party,
            amount_paise=amount,
            allocations=allocations,
            narration=_t(v, "NARRATION"),
        ))
    return out


def parse_stock(raw: str) -> list[StockItemRecord]:
    out = []
    for el in parse_envelope(raw).iterfind(".//COLLECTION/STOCKITEM"):
        qty, unit = parse_qty(_t(el, "CLOSINGBALANCE"))
        prices = el.findall("STANDARDPRICELIST.LIST")
        if prices:
            latest = max(prices, key=lambda p: _t(p, "DATE"))
            rate = parse_rate(_t(latest, "RATE"))
        else:
            rate = parse_rate(_t(el, "CLOSINGRATE"))
        out.append(StockItemRecord(
            name=_name(el),
            group=_t(el, "PARENT"),
            unit=_t(el, "BASEUNITS") or unit,
            qty=qty,
            rate_paise=rate,
        ))
    return out


# -------------------------------------------------------------------- connector

def http_transport(url: str, timeout_s: float) -> Transport:
    def post(body: str) -> str:
        try:
            resp = httpx.post(
                url,
                content=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=timeout_s,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise ConnectorUnavailable(
                f"Tally is not reachable at {url}. Is TallyPrime (or the mock) running "
                f"with the XML server enabled on that port?"
            ) from e
        except httpx.TimeoutException as e:
            raise ConnectorUnavailable(f"Tally at {url} timed out after {timeout_s}s") from e
        except httpx.HTTPError as e:
            raise ConnectorUnavailable(f"Tally at {url}: {e}") from e
        if resp.status_code != 200:
            raise ConnectorError(f"Tally at {url} returned HTTP {resp.status_code}")
        return resp.content.decode("utf-8", errors="replace")

    return post


class TallyXmlConnector(Connector):
    name = "tally_xml"

    def __init__(self, url: str = "http://localhost:9000", company: str = "",
                 timeout_s: float = 15.0, transport: Transport | None = None):
        self.url = url
        self.company = company
        self._post = transport or http_transport(url, timeout_s)
        self._credit_days: dict[str, int | None] = {}

    def _export(self, cid: str, otype: str, fetch: list[str], **kw) -> str:
        return self._post(build_collection_request(cid, otype, fetch, company=self.company, **kw))

    def fetch_company(self) -> CompanyInfo:
        return parse_company(self._export(C_COMPANY, "Company", ["NAME", "ADDRESS", "GSTREGISTRATIONNUMBER"]))

    def fetch_ledgers(self) -> list[LedgerRecord]:
        ledgers = parse_ledgers(self._export(
            C_LEDGERS, "Ledger",
            ["NAME", "PARENT", "ADDRESS", "LEDGERPHONE", "LEDGERMOBILE", "EMAIL",
             "CREDITLIMIT", "BILLCREDITPERIOD", "CLOSINGBALANCE"],
            child_of="$$GroupSundryDebtors",
        ))
        self._credit_days = {l.name: l.credit_days for l in ledgers}
        return ledgers

    def fetch_sales_invoices(self, from_date: date, to_date: date) -> list[SalesInvoiceRecord]:
        return parse_sales_vouchers(self._export(
            C_SALES, "Voucher",
            ["DATE", "VOUCHERNUMBER", "PARTYLEDGERNAME", "NARRATION",
             "ALLINVENTORYENTRIES", "LEDGERENTRIES", "ALLLEDGERENTRIES"],
            filters={F_IS_SALES: "$$IsSales:$VoucherTypeName"},
            from_date=from_date, to_date=to_date,
        ))

    def fetch_outstanding_bills(self) -> list[OutstandingBillRecord]:
        return parse_bills(self._export(
            C_BILLS, "Bill",
            ["NAME", "PARENT", "BILLDATE", "BILLCREDITPERIOD", "CLOSINGBALANCE"],
            filters={F_IS_RECEIVABLE: "$$IsDebit:$ClosingBalance"},
        ), self._credit_days)

    def fetch_receipts(self, from_date: date, to_date: date) -> list[ReceiptRecord]:
        return parse_receipts(self._export(
            C_RECEIPTS, "Voucher",
            ["DATE", "VOUCHERNUMBER", "PARTYLEDGERNAME", "NARRATION", "ALLLEDGERENTRIES"],
            filters={F_IS_RECEIPT: "$$IsReceipt:$VoucherTypeName"},
            from_date=from_date, to_date=to_date,
        ))

    def fetch_stock(self) -> list[StockItemRecord]:
        return parse_stock(self._export(
            C_STOCK, "StockItem",
            ["NAME", "PARENT", "BASEUNITS", "CLOSINGBALANCE", "CLOSINGRATE", "STANDARDPRICELIST"],
        ))
