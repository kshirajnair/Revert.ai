"""XML request/parse behaviour, and render(mock) -> parse(connector) round trips."""
from datetime import date

import pytest

from revert.connectors.base import ConnectorError
from revert.connectors.tally_xml import (
    build_collection_request,
    parse_amount,
    parse_bills,
    parse_envelope,
    parse_ledgers,
    parse_qty,
    parse_rate,
    parse_stock,
    sanitize_xml,
)
from revert.mock_tally import xml_render as R
from revert.mock_tally.server import handle_xml
from tests.conftest import AS_OF


def test_request_is_export_only_and_well_formed():
    xml = build_collection_request("X", "Voucher", ["DATE"], company="Acme",
                                   filters={"F": "$$IsSales:$VoucherTypeName"},
                                   from_date=date(2026, 1, 1), to_date=date(2026, 3, 31))
    root = parse_envelope(xml)
    assert root.findtext("HEADER/TALLYREQUEST") == "Export"
    assert root.findtext(".//SVFROMDATE") == "20260101"
    assert root.find(".//SYSTEM[@NAME='F']").text == "$$IsSales:$VoucherTypeName"


def test_parse_helpers():
    assert parse_amount("-45000.00") == -4_500_000
    assert parse_amount("45,000.00 Dr") == -4_500_000
    assert parse_amount("") == 0
    assert parse_qty(" 420 Nos") == (420.0, "Nos")
    assert parse_qty("1,250.5 Mtr") == (1250.5, "Mtr")
    assert parse_rate("1180.00/Nos") == 118_000


def test_sanitize_strips_invalid_chars():
    raw = "<ENVELOPE><A>ok&#4;\x01</A><B>&#10;keep</B></ENVELOPE>"
    root = parse_envelope(raw)
    assert root.findtext("A") == "ok"
    assert "&#10;" in sanitize_xml(raw)


def test_line_error_raises():
    with pytest.raises(ConnectorError, match="Tally error"):
        parse_envelope(R.error_envelope("Could not find Collection"))


def test_malformed_xml_raises():
    with pytest.raises(ConnectorError, match="malformed"):
        parse_envelope("<ENVELOPE><unclosed>")


def test_mock_rejects_non_export_requests(dataset):
    body = "<ENVELOPE><HEADER><TALLYREQUEST>Import</TALLYREQUEST></HEADER></ENVELOPE>"
    with pytest.raises(ConnectorError, match="read-only"):
        parse_envelope(handle_xml(dataset, body))


def test_ledgers_round_trip_with_sign_flip(dataset):
    parsed = {l.name: l for l in parse_ledgers(R.render_ledgers(dataset))}
    for l in dataset.ledgers_with_balances():
        p = parsed[l.name]
        assert p.closing_balance_paise == l.closing_balance_paise  # Dr negative in XML, positive for us
        assert p.credit_limit_paise == l.credit_limit_paise
        assert p.credit_days == l.credit_days
        assert p.phones == l.phones


def test_bills_round_trip(dataset):
    assert parse_bills(R.render_bills(dataset)) == dataset.outstanding_bills()


def test_stock_round_trip(dataset):
    assert parse_stock(R.render_stock(dataset, AS_OF)) == dataset.stock


def test_sales_and_receipts_round_trip(connector, dataset):
    invoices = connector.fetch_sales_invoices(date(2025, 1, 1), AS_OF)
    assert invoices == dataset.invoices
    receipts = connector.fetch_receipts(date(2025, 1, 1), AS_OF)
    assert receipts == dataset.receipts


def test_voucher_date_window(connector):
    invs = connector.fetch_sales_invoices(date(2026, 10, 1), AS_OF)
    assert invs and all(i.date >= date(2026, 10, 1) for i in invs)


def test_wrong_company_is_an_error(dataset):
    from revert.connectors.mock_tally import MockTallyConnector

    c = MockTallyConnector(company="Some Other Co", dataset=dataset)
    with pytest.raises(ConnectorError, match="not loaded"):
        c.fetch_ledgers()
