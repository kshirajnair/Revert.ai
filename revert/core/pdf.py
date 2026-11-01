"""Invoice and statement PDFs, written directly (no PDF library, no new dependency).

Plain A4 pages with the built-in Helvetica font. That font has no ₹ glyph, so amounts read
"Rs. 22,770.00". Every figure comes straight from the DB rows passed in.
"""
from __future__ import annotations

from datetime import date

from revert.core.models import Buyer, Company, Invoice, Receipt
from revert.core.money import format_inr

A4 = (595, 842)
MARGIN = 48
# Helvetica advance widths (per 1000 units) for the characters that appear in numbers
_WIDTHS = {**{d: 556 for d in "0123456789"}, ",": 278, ".": 278, " ": 278, "R": 722, "s": 500, "-": 333}


def rs(paise: int) -> str:
    return format_inr(paise, show_paise=True).replace("₹", "Rs. ")


def _width(text: str, size: float) -> float:
    return sum(_WIDTHS.get(c, 556) for c in text) * size / 1000


def _esc(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class PDF:
    def __init__(self):
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self.new_page()

    def new_page(self) -> None:
        self.pages.append([])
        self.y = A4[1] - MARGIN

    def text(self, x: float, y: float, s: str, size: float = 10, bold: bool = False) -> None:
        font = b"/F2" if bold else b"/F1"
        self.pages[-1].append(b"BT " + font + b" %.1f Tf %.1f %.1f Td (" % (size, x, y) + _esc(s) + b") Tj ET")

    def right(self, x_right: float, y: float, s: str, size: float = 10, bold: bool = False) -> None:
        self.text(x_right - _width(s, size), y, s, size, bold)

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.pages[-1].append(b"0.6 w %.1f %.1f m %.1f %.1f l S" % (x1, y1, x2, y2))

    def need(self, height: float) -> None:
        if self.y - height < MARGIN:
            self.new_page()

    def build(self) -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        pages_id = len(objs) + 1 + 2 * len(self.pages)  # reserved: after every page + content pair
        kids = []
        for ops in self.pages:
            stream = b"\n".join(ops)
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                            % (pages_id, A4[0], A4[1], content, f1, f2)))
        assert add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                   + b"] /Count %d >>" % len(kids)) == pages_id
        catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
        return bytes(out)


def _header(pdf: PDF, company: Company, title: str, buyer: Buyer) -> None:
    w = A4[0] - MARGIN
    pdf.text(MARGIN, pdf.y, company.name, 16, bold=True)
    pdf.right(w, pdf.y, title, 14, bold=True)
    pdf.y -= 16
    if company.address:
        pdf.text(MARGIN, pdf.y, company.address, 9)
        pdf.y -= 12
    if company.gstin:
        pdf.text(MARGIN, pdf.y, f"GSTIN: {company.gstin}", 9)
        pdf.y -= 12
    pdf.y -= 10
    pdf.text(MARGIN, pdf.y, "To:", 9)
    pdf.text(MARGIN + 24, pdf.y, buyer.ledger_name, 11, bold=True)
    pdf.y -= 13
    if buyer.address:
        pdf.text(MARGIN + 24, pdf.y, buyer.address, 9)
        pdf.y -= 12


def _footer(pdf: PDF, synced_text: str) -> None:
    pdf.need(30)
    pdf.y -= 18
    pdf.text(MARGIN, pdf.y, f"Copy from our books, updated {synced_text}. Not a fresh tax invoice.", 8)


def invoice_pdf(company: Company, buyer: Buyer, inv: Invoice, synced_text: str) -> bytes:
    pdf = PDF()
    w = A4[0] - MARGIN
    _header(pdf, company, "TAX INVOICE (COPY)", buyer)
    pdf.y -= 6
    pdf.text(MARGIN, pdf.y, f"Invoice {inv.number}", 11, bold=True)
    pdf.right(w, pdf.y, f"Date {inv.date:%d %b %Y}   Due {inv.due_date:%d %b %Y}", 10)
    pdf.y -= 20
    cols = (MARGIN, 330, 400, 470, w)  # item | qty | rate | amount (right edges after the first)
    pdf.text(cols[0], pdf.y, "Item", 9, bold=True)
    pdf.right(cols[2], pdf.y, "Qty", 9, bold=True)
    pdf.right(cols[3], pdf.y, "Rate", 9, bold=True)
    pdf.right(cols[4], pdf.y, "Amount", 9, bold=True)
    pdf.y -= 5
    pdf.line(MARGIN, pdf.y, w, pdf.y)
    pdf.y -= 13
    for line in inv.lines:
        pdf.need(16)
        qty = f"{line.qty:g} {line.unit}".strip()
        pdf.text(cols[0], pdf.y, line.item_name[:48], 10)
        pdf.right(cols[2], pdf.y, qty, 10)
        pdf.right(cols[3], pdf.y, rs(line.rate_paise), 10)
        pdf.right(cols[4], pdf.y, rs(line.amount_paise), 10)
        pdf.y -= 15
    pdf.line(MARGIN, pdf.y + 8, w, pdf.y + 8)
    pdf.y -= 4
    for label, paise, bold in (("Subtotal", inv.subtotal_paise, False), ("GST", inv.tax_paise, False),
                               ("Total", inv.total_paise, True), ("Paid", inv.paid_paise, False),
                               ("Pending", inv.balance_paise, True)):
        pdf.need(16)
        pdf.right(cols[3], pdf.y, label, 10, bold)
        pdf.right(cols[4], pdf.y, rs(paise), 10, bold)
        pdf.y -= 15
    if inv.narration:
        pdf.y -= 6
        pdf.text(MARGIN, pdf.y, inv.narration[:90], 9)
    _footer(pdf, synced_text)
    return pdf.build()


def statement_pdf(company: Company, buyer: Buyer, open_invoices: list[Invoice], receipts: list[Receipt],
                  as_of: date, synced_text: str) -> bytes:
    pdf = PDF()
    w = A4[0] - MARGIN
    _header(pdf, company, "STATEMENT", buyer)
    pdf.y -= 6
    pdf.text(MARGIN, pdf.y, f"Open invoices as of {as_of:%d %b %Y}", 11, bold=True)
    pdf.y -= 18
    heads = ((MARGIN, "Invoice"), (140, "Date"), (230, "Due"))
    for x, h in heads:
        pdf.text(x, pdf.y, h, 9, bold=True)
    pdf.right(420, pdf.y, "Invoice total", 9, bold=True)
    pdf.right(w, pdf.y, "Pending", 9, bold=True)
    pdf.y -= 5
    pdf.line(MARGIN, pdf.y, w, pdf.y)
    pdf.y -= 13
    for inv in open_invoices:
        pdf.need(16)
        pdf.text(MARGIN, pdf.y, inv.number, 10)
        pdf.text(140, pdf.y, f"{inv.date:%d %b %Y}", 10)
        pdf.text(230, pdf.y, f"{inv.due_date:%d %b %Y}", 10)
        pdf.right(420, pdf.y, rs(inv.total_paise), 10)
        pdf.right(w, pdf.y, rs(inv.balance_paise), 10)
        pdf.y -= 15
    if not open_invoices:
        pdf.text(MARGIN, pdf.y, "Nothing pending.", 10)
        pdf.y -= 15
    pdf.line(MARGIN, pdf.y + 8, w, pdf.y + 8)
    pdf.y -= 4
    pdf.right(420, pdf.y, "Total pending", 10, bold=True)
    pdf.right(w, pdf.y, rs(sum(i.balance_paise for i in open_invoices)), 10, bold=True)
    pdf.y -= 28
    if receipts:
        pdf.need(40)
        pdf.text(MARGIN, pdf.y, "Recent payments received", 11, bold=True)
        pdf.y -= 18
        for r in receipts:
            pdf.need(16)
            pdf.text(MARGIN, pdf.y, f"{r.date:%d %b %Y}", 10)
            pdf.text(140, pdf.y, (r.narration or r.voucher_number)[:45], 10)
            pdf.right(w, pdf.y, rs(r.amount_paise), 10)
            pdf.y -= 15
    _footer(pdf, synced_text)
    return pdf.build()
