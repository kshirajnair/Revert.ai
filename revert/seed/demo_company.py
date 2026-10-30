"""Demo books for Shree Ganesh Electricals, Peenya, Bangalore (switchgear distributor).

Everything is dated relative to `as_of`, so the demo always looks current, and is
deterministic for a given seed. Three stories are planted:

1. SHARMA    - loyal dealer, always pays on time, one invoice 4 days late. Tone: gentle.
2. VENKATESH - disputes INV-1043: billed RCCB at ₹1,180, PO said ₹1,150.
3. MAHADEV   - habitually late payer, exactly ₹80,000 pending across three overdue invoices.

Phone numbers are deliberately invalid (+91 00000 xxxxx) so nothing can reach a real person.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from revert.connectors.base import (
    AllocationRecord,
    CompanyInfo,
    InvoiceLineRecord,
    LedgerRecord,
    ReceiptRecord,
    SalesInvoiceRecord,
    StockItemRecord,
)
from revert.mock_tally.dataset import TallyDataset

COMPANY = CompanyInfo(
    name="Shree Ganesh Electricals",
    address="Plot 42, 3rd Phase, Peenya Industrial Area, Bangalore 560058",
    gstin="29AAAAA0000A1Z5",
)

SHARMA = "Sharma Electricals"
VENKATESH = "Venkatesh Power Solutions"
MAHADEV = "Mahadev Traders"
DISPUTED_INVOICE = "INV-1043"
MAHADEV_PENDING_PAISE = 80_000_00

# name, group, unit, closing qty, selling rate (₹), typical order qty range
STOCK = [
    ("MCB SP 6A C-Curve", "MCBs", "Nos", 420, 185, (20, 100)),
    ("MCB SP 16A C-Curve", "MCBs", "Nos", 650, 195, (20, 120)),
    ("MCB SP 32A C-Curve", "MCBs", "Nos", 380, 215, (20, 80)),
    ("MCB DP 32A C-Curve", "MCBs", "Nos", 160, 640, (5, 30)),
    ("MCB TP 63A C-Curve", "MCBs", "Nos", 45, 2450, (2, 10)),
    ("RCCB 4P 63A 30mA", "RCCBs", "Nos", 28, 1180, (4, 20)),
    ("Isolator DP 63A", "Isolators", "Nos", 70, 520, (5, 20)),
    ("Distribution Board 8-Way DP", "Distribution Boards", "Nos", 55, 1650, (2, 10)),
    ("Modular Switch 6A 1-Way", "Switches", "Nos", 2400, 48, (100, 500)),
    ("Modular Socket 6A 3-Pin", "Switches", "Nos", 1500, 95, (50, 300)),
    ("Modular Plate 8M", "Switches", "Nos", 600, 165, (20, 100)),
    ("FR PVC Wire 1.5 sq mm (90 m)", "Wires", "Coil", 140, 1350, (5, 25)),
    ("FR PVC Wire 2.5 sq mm (90 m)", "Wires", "Coil", 110, 2150, (5, 20)),
    ("FR PVC Wire 4.0 sq mm (90 m)", "Wires", "Coil", 60, 3400, (2, 12)),
    ("Changeover Switch 63A 4P", "Changeovers", "Nos", 0, 3900, (1, 4)),
]


@dataclass(frozen=True)
class DealerSpec:
    name: str
    area: str
    language: str  # en | hi | kn  (Revert-owned preference, not in Tally)
    limit_rupees: int
    credit_days: int
    profile: str  # planted | prompt | slow | chronic
    tone: str = "standard"


DEALERS = [
    DealerSpec(SHARMA, "Rajajinagar", "en", 300_000, 30, "planted", tone="gentle"),
    DealerSpec(VENKATESH, "Yeshwanthpur", "kn", 200_000, 30, "planted"),
    DealerSpec(MAHADEV, "Peenya 2nd Stage", "hi", 150_000, 30, "planted"),
    DealerSpec("Sri Lakshmi Electricals", "Malleshwaram", "kn", 250_000, 30, "prompt"),
    DealerSpec("Patel Electric House", "Chickpet", "hi", 400_000, 45, "slow"),
    DealerSpec("Raghavendra Enterprises", "Vijayanagar", "kn", 150_000, 30, "slow"),
    DealerSpec("Gupta Switchgear Mart", "KR Market", "hi", 500_000, 45, "prompt"),
    DealerSpec("Annapoorna Electricals", "Basaveshwaranagar", "kn", 250_000, 30, "chronic"),
    DealerSpec("Jain Electrical Stores", "Avenue Road", "hi", 350_000, 30, "slow"),
    DealerSpec("Manjunath Electricals", "Jalahalli", "kn", 125_000, 15, "prompt"),
    DealerSpec("Bharat Wires & Cables", "Dasarahalli", "en", 300_000, 30, "chronic"),
    DealerSpec("Kaveri Lighting Solutions", "Nagarbhavi", "kn", 75_000, 30, "slow"),
    DealerSpec("Agarwal Electricals", "Seshadripuram", "hi", 200_000, 30, "prompt"),
    DealerSpec("New Balaji Electricals", "Hebbal", "en", 150_000, 30, "slow"),
    DealerSpec("Siddhi Vinayak Traders", "Magadi Road", "hi", 50_000, 15, "chronic"),
    DealerSpec("Tejas Electro Systems", "Yelahanka", "en", 250_000, 45, "prompt"),
    DealerSpec("Om Sai Electricals", "Kengeri", "hi", 200_000, 30, "slow"),
    DealerSpec("Nandi Electricals", "Tumkur Road", "kn", 300_000, 30, "chronic"),
    DealerSpec("Reddy Power Systems", "Hosur Road", "en", 450_000, 45, "prompt"),
    DealerSpec("Basava Electricals", "Rajarajeshwari Nagar", "kn", 125_000, 30, "slow"),
    DealerSpec("Shiv Shakti Electricals", "Chamrajpet", "hi", 200_000, 30, "slow"),
    DealerSpec("Prakash Electrical Agencies", "Banashankari", "en", 200_000, 30, "prompt"),
    DealerSpec("Durga Electricals", "Mathikere", "kn", 150_000, 15, "chronic"),
    DealerSpec("Sai Krishna Enterprises", "Sunkadakatte", "en", 150_000, 30, "slow"),
    DealerSpec("Mehta Electricals", "Jayanagar", "hi", 300_000, 30, "prompt"),
]

DEMO_PREFS = {d.name: {"language": d.language, "tone": d.tone} for d in DEALERS}

N_INVOICES = 100
FIRST_INVOICE_NO = 1001
OLDEST_DAYS_AGO = 130  # a handful predate 90 days so the 90+ bucket is populated


def demo_phone(index: int) -> str:
    return f"+9100000{index:05d}"


def _gst_invoice(number, d, party, lines, narration="") -> SalesInvoiceRecord:
    subtotal = sum(l.amount_paise for l in lines)
    cgst = (subtotal * 9 + 50) // 100  # 9% CGST + 9% SGST (intra-state, Karnataka)
    tax = 2 * cgst
    total = ((subtotal + tax + 50) // 100) * 100  # Tally round-off to the rupee
    return SalesInvoiceRecord(
        number=number,
        date=d,
        party_name=party,
        total_paise=total,
        lines=lines,
        subtotal_paise=subtotal,
        tax_paise=tax,
        narration=narration,
    )


def build_dataset(as_of: date, seed: int = 42) -> TallyDataset:
    rng = random.Random(seed)
    stock_by_name = {s[0]: s for s in STOCK}

    def line(item: str, qty: int, rate_rupees: int | None = None) -> InvoiceLineRecord:
        _, _, unit, _, rate, _ = stock_by_name[item]
        rate_paise = (rate_rupees or rate) * 100
        return InvoiceLineRecord(item, float(qty), unit, rate_paise, qty * rate_paise)

    def random_lines() -> list[InvoiceLineRecord]:
        items = rng.sample(STOCK, rng.randint(1, 3))
        return [line(s[0], rng.randint(*s[5])) for s in items]

    # 100 slots, oldest first, so invoice numbers increase with date like real books.
    slot_days_ago = [OLDEST_DAYS_AGO - (i * (OLDEST_DAYS_AGO - 2)) // (N_INVOICES - 1) for i in range(N_INVOICES)]
    taken: dict[int, dict] = {}

    def claim(days_ago: int, **plan) -> None:
        free = [i for i in range(N_INVOICES) if i not in taken]
        idx = min(free, key=lambda i: abs(slot_days_ago[i] - days_ago))
        taken[idx] = plan

    disputed_slot = int(DISPUTED_INVOICE.split("-")[1]) - FIRST_INVOICE_NO
    taken[disputed_slot] = dict(
        party=VENKATESH,
        lines=[line("RCCB 4P 63A 30mA", 20, rate_rupees=1180)],
        narration="Against buyer PO VPS/PO/0112",
        pay="none",
    )
    # Story 1: Sharma pays on time, every time... except the latest due one (4 days late).
    for days in (125, 100, 78, 55):
        claim(days, party=SHARMA, pay="on_time")
    claim(34, party=SHARMA, pay="none", due=as_of - timedelta(days=4))
    claim(12, party=SHARMA, pay="none")
    # Story 2: Venkatesh pays everything except the disputed bill.
    for days in (110, 60):
        claim(days, party=VENKATESH, pay="on_time")
    claim(20, party=VENKATESH, pay="none")
    # Story 3: Mahadev, paid late historically, now exactly ₹80,000 pending, all overdue.
    for days in (120, 95):
        claim(days, party=MAHADEV, pay="late")
    claim(80, party=MAHADEV, pay="mahadev_part",
          lines=[line("FR PVC Wire 2.5 sq mm (90 m)", 8), line("MCB DP 32A C-Curve", 12)])
    claim(60, party=MAHADEV, pay="none",
          lines=[line("FR PVC Wire 4.0 sq mm (90 m)", 6), line("Distribution Board 8-Way DP", 4)])
    claim(40, party=MAHADEV, pay="none", lines=[line("FR PVC Wire 2.5 sq mm (90 m)", 10)])

    dealers = {d.name: d for d in DEALERS}
    others = [d for d in DEALERS if d.profile != "planted"]

    invoices: list[SalesInvoiceRecord] = []
    due_dates: dict[str, date] = {}
    payments: list[tuple[date, str, int, str]] = []  # (date, party, paise, invoice_no)
    mahadev_part_invoice = None

    for i in range(N_INVOICES):
        number = f"INV-{FIRST_INVOICE_NO + i}"
        inv_date = as_of - timedelta(days=slot_days_ago[i])
        plan = taken.get(i) or {"party": rng.choice(others).name, "pay": "profile"}
        dealer = dealers[plan["party"]]
        inv = _gst_invoice(number, inv_date, dealer.name, plan.get("lines") or random_lines(),
                           plan.get("narration", ""))
        due = plan.get("due") or inv_date + timedelta(days=dealer.credit_days)
        invoices.append(inv)
        due_dates[number] = due

        pay_on, pay_amt = None, inv.total_paise
        mode = plan["pay"] if plan["pay"] != "profile" else dealer.profile
        if mode == "on_time":
            pay_on = inv_date + timedelta(days=rng.randint(max(3, dealer.credit_days - 10), dealer.credit_days))
        elif mode in ("late", "slow"):
            pay_on = due + timedelta(days=rng.randint(5, 40))
            if mode == "slow" and rng.random() < 0.25:
                pay_amt = _part(inv.total_paise, rng)
        elif mode == "prompt":
            pay_on = inv_date + timedelta(days=rng.randint(max(3, dealer.credit_days - 10), dealer.credit_days))
        elif mode == "chronic":
            pay_on = due + timedelta(days=rng.randint(30, 150))
            if rng.random() < 0.4:
                pay_amt = _part(inv.total_paise, rng)
        elif mode == "mahadev_part":
            mahadev_part_invoice = (inv, due)
        if pay_on and pay_on <= as_of:
            payments.append((pay_on, dealer.name, pay_amt, number))

    # Mahadev's part payment is sized so his pending is exactly ₹80,000.
    mahadev_total = sum(
        inv.total_paise for inv in invoices
        if inv.party_name == MAHADEV and not any(p[3] == inv.number for p in payments)
    )
    inv, due = mahadev_part_invoice
    payments.append((due + timedelta(days=20), MAHADEV, mahadev_total - MAHADEV_PENDING_PAISE, inv.number))

    receipts = []
    for n, (on, party, paise, inv_no) in enumerate(sorted(payments), start=1):
        receipts.append(ReceiptRecord(
            voucher_number=f"RCT-{n:04d}",
            date=on,
            party_name=party,
            amount_paise=paise,
            allocations=[AllocationRecord(inv_no, paise)],
            narration=_utr(rng),
        ))

    ledgers = [
        LedgerRecord(
            name=d.name,
            parent="Sundry Debtors",
            phones=[demo_phone(i)],
            address=f"{d.area}, Bangalore",
            credit_limit_paise=d.limit_rupees * 100,
            credit_days=d.credit_days,
        )
        for i, d in enumerate(DEALERS, start=1)
    ]
    stock = [StockItemRecord(n, g, u, float(q), r * 100) for n, g, u, q, r, _ in STOCK]
    ds = TallyDataset(COMPANY, ledgers, invoices, due_dates, receipts, stock)
    ds.ledgers = ds.ledgers_with_balances()
    return ds


def _part(total_paise: int, rng: random.Random) -> int:
    pct = rng.uniform(0.4, 0.7)
    return max(500_00, int(total_paise * pct) // 500_00 * 500_00)  # round down to ₹500


def _utr(rng: random.Random) -> str:
    kind = rng.choice(["NEFT", "UPI", "RTGS", "CHQ"])
    if kind == "UPI":
        return f"UPI Ref {rng.randint(10**11, 10**12 - 1)}"
    if kind == "CHQ":
        return f"Cheque No. {rng.randint(100000, 999999)}"
    return f"{kind} UTR SBIN{rng.randint(10**11, 10**12 - 1)}"
