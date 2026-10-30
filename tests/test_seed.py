"""The demo data must actually contain the stories the demo script relies on."""
from collections import Counter

from revert.core.ageing import Bucket, age_invoices, ageing_report
from revert.core.sync import run_sync
from revert.seed.demo_company import (
    DEMO_PREFS,
    DISPUTED_INVOICE,
    MAHADEV,
    MAHADEV_PENDING_PAISE,
    SHARMA,
    VENKATESH,
    build_dataset,
)
from tests.conftest import AS_OF


def test_sizes(dataset):
    assert len(dataset.ledgers) == 25
    assert len(dataset.invoices) == 100
    assert len(dataset.stock) == 15
    assert all(50_000_00 <= l.credit_limit_paise <= 5_00_000_00 for l in dataset.ledgers)
    assert set(Counter(p["language"] for p in DEMO_PREFS.values())) == {"en", "hi", "kn"}


def test_deterministic():
    a, b = build_dataset(AS_OF), build_dataset(AS_OF)
    assert a.invoices == b.invoices and a.receipts == b.receipts


def test_phones_are_unreachable_placeholders(dataset):
    for l in dataset.ledgers:
        assert l.phones[0].startswith("+9100000")  # 0-prefixed: not a valid Indian mobile


def test_invoice_status_mix(dataset):
    pending = dataset.pending_by_invoice()
    states = Counter(
        "paid" if pending[i.number] == 0 else "part" if pending[i.number] < i.total_paise else "unpaid"
        for i in dataset.invoices
    )
    assert states["paid"] >= 30 and states["part"] >= 3 and states["unpaid"] >= 20


def _synced(session, connector):
    res = run_sync(session, connector, as_of=AS_OF)
    assert res.ok, res.error
    return res.company_id


def test_every_ageing_bucket_populated(session, connector):
    cid = _synced(session, connector)
    rows = ageing_report(session, cid, AS_OF)
    for bucket in Bucket:
        assert sum(r.buckets[bucket] for r in rows) > 0, bucket


def test_story_sharma_loyal_slightly_late(session, connector):
    cid = _synced(session, connector)
    sharma = next(r for r in ageing_report(session, cid, AS_OF) if r.name == SHARMA)
    overdue = [i for i in sharma.invoices if i.days_overdue >= 0]
    assert len(overdue) == 1 and overdue[0].days_overdue == 4
    assert DEMO_PREFS[SHARMA]["tone"] == "gentle"


def test_story_venkatesh_disputed_invoice(session, connector):
    from sqlalchemy import select

    from revert.core.models import Invoice

    _synced(session, connector)
    inv = session.scalar(select(Invoice).where(Invoice.number == DISPUTED_INVOICE))
    assert inv.buyer.ledger_name == VENKATESH
    assert inv.status == "unpaid"
    assert [(l.item_name, l.rate_paise) for l in inv.lines] == [("RCCB 4P 63A 30mA", 1_180_00)]
    assert "PO" in inv.narration
    assert age_invoices([inv], AS_OF)[0].days_overdue > 30


def test_story_mahadev_heavy_overdue(session, connector):
    cid = _synced(session, connector)
    m = next(r for r in ageing_report(session, cid, AS_OF) if r.name == MAHADEV)
    assert m.total_outstanding_paise == MAHADEV_PENDING_PAISE == 80_000_00
    assert m.total_overdue_paise == 80_000_00
    assert len(m.invoices) == 3 and m.oldest_days_overdue >= 45
