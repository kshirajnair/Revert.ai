"""The Tally XML connector pointed at the mock server.

Same request/parse code path as real Tally. Adds two mock-only conveniences:
- `in_process=True`: answer requests by calling the mock handler directly (no HTTP),
  still going through the full XML render -> parse round trip.
- `record_payment()`: post a receipt into the mock's books, for demoing "payment syncs".
"""
from __future__ import annotations

from datetime import date

import httpx

from revert.connectors.base import ConnectorError, ConnectorUnavailable
from revert.connectors.tally_xml import TallyXmlConnector
from revert.mock_tally.dataset import TallyDataset


class MockTallyConnector(TallyXmlConnector):
    name = "mock_tally"

    def __init__(self, url: str = "http://localhost:9000", company: str = "", timeout_s: float = 15.0,
                 dataset: TallyDataset | None = None, today: date | None = None):
        transport = None
        if dataset is not None:
            from revert.mock_tally.server import handle_xml

            transport = lambda body: handle_xml(dataset, body, today)  # noqa: E731
        super().__init__(url=url, company=company, timeout_s=timeout_s, transport=transport)
        self.dataset = dataset
        self.timeout_s = timeout_s

    def record_payment(self, party: str, amount_rupees: float, invoice: str | None = None,
                       narration: str = "Demo payment") -> dict:
        if self.dataset is not None:
            from revert.core.timeutil import today_ist
            from revert.core.money import rupees_to_paise

            r = self.dataset.add_receipt(party, rupees_to_paise(amount_rupees), today_ist(), invoice, narration)
            return {"ok": True, "voucher_number": r.voucher_number}
        try:
            resp = httpx.post(f"{self.url.rstrip('/')}/_mock/receipt", timeout=self.timeout_s, json={
                "party": party, "amount": amount_rupees, "invoice": invoice, "narration": narration,
            })
        except httpx.HTTPError as e:
            raise ConnectorUnavailable(f"Mock Tally not reachable at {self.url}") from e
        data = resp.json()
        if not data.get("ok"):
            raise ConnectorError(data.get("error", "mock rejected payment"))
        return data
