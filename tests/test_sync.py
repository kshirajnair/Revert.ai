import socket

from sqlalchemy import func, select

from revert.connectors.mock_tally import MockTallyConnector
from revert.connectors.tally_xml import TallyXmlConnector
from revert.core.models import Buyer, BuyerPhone, Company, Invoice, Receipt, StockItem, SyncRun
from revert.core.sync import run_sync
from revert.mock_tally.server import start_in_thread
from revert.seed.demo_company import MAHADEV, build_dataset
from tests.conftest import AS_OF


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_initial_sync_counts(session, connector):
    res = run_sync(session, connector, as_of=AS_OF)
    assert res.ok
    assert (_count(session, Buyer), _count(session, Invoice), _count(session, StockItem)) == (25, 100, 15)
    assert _count(session, Receipt) == len(connector.dataset.receipts)
    assert session.scalar(select(Company)).last_synced_at is not None


def test_sync_is_idempotent(session, connector):
    run_sync(session, connector, as_of=AS_OF)
    res = run_sync(session, connector, as_of=AS_OF)
    assert res.ok and res.paid_invoices == []
    assert _count(session, Invoice) == 100
    assert _count(session, BuyerPhone) == 25


def test_balances_match_tally(session, connector, dataset):
    run_sync(session, connector, as_of=AS_OF)
    pending = dataset.pending_by_invoice()
    for inv in session.scalars(select(Invoice)):
        assert inv.balance_paise == pending[inv.number]
    for b in session.scalars(select(Buyer)):
        assert b.closing_balance_paise == sum(i.balance_paise for i in b.invoices)


def test_payment_is_reported_and_balance_drops(session, connector, dataset):
    run_sync(session, connector, as_of=AS_OF)
    inv = session.scalar(select(Invoice).join(Buyer).where(Buyer.ledger_name == MAHADEV, Invoice.balance_paise > 0)
                         .order_by(Invoice.date))
    before = inv.balance_paise
    dataset.add_receipt(MAHADEV, 10_000_00, AS_OF, inv.number, "UTR TEST123")
    res = run_sync(session, connector, as_of=AS_OF)
    assert res.paid_invoices == [inv.number]
    session.refresh(inv)
    assert inv.balance_paise == before - 10_000_00
    r = session.scalar(select(Receipt).where(Receipt.narration == "UTR TEST123"))
    assert r.allocations[0].invoice_number == inv.number


def test_revert_owned_fields_survive_sync(session, connector):
    run_sync(session, connector, as_of=AS_OF)
    b = session.scalar(select(Buyer).where(Buyer.ledger_name == MAHADEV))
    b.tone, b.paused, b.language = "firm", True, "hi"
    b.phones.append(BuyerPhone(phone="+910000099999", source="manual"))
    session.commit()
    run_sync(session, connector, as_of=AS_OF)
    session.refresh(b)
    assert (b.tone, b.paused, b.language) == ("firm", True, "hi")
    assert {p.phone for p in b.phones} == {"+910000000003", "+910000099999"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_tally_not_running_fails_gracefully_and_keeps_data(session, connector):
    run_sync(session, connector, as_of=AS_OF)
    synced_at = session.scalar(select(Company)).last_synced_at
    down = TallyXmlConnector(f"http://127.0.0.1:{_free_port()}", timeout_s=2)
    res = run_sync(session, down, as_of=AS_OF)
    assert not res.ok and "not reachable" in res.error
    assert _count(session, Invoice) == 100
    assert session.scalar(select(Company)).last_synced_at == synced_at
    assert session.scalar(select(SyncRun).order_by(SyncRun.id.desc())).status == "failed"


def test_sync_over_real_http_against_mock_server(session):
    srv = start_in_thread(lambda: build_dataset(AS_OF))
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        conn = MockTallyConnector(url, timeout_s=10)
        res = run_sync(session, conn, as_of=AS_OF)
        assert res.ok, res.error
        assert res.counts["invoices"] == 100
        # payment through the mock's admin endpoint, then re-sync stops showing it as pending
        inv = session.scalar(select(Invoice).join(Buyer).where(Buyer.ledger_name == MAHADEV,
                                                                Invoice.balance_paise > 0))
        conn.record_payment(MAHADEV, inv.balance_paise / 100, inv.number, "UTR HTTP1")
        res = run_sync(session, conn, as_of=AS_OF)
        assert inv.number in res.paid_invoices
        session.refresh(inv)
        assert inv.status == "paid"
    finally:
        srv.shutdown()
        srv.server_close()
