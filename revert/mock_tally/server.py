"""A tiny HTTP server that mimics TallyPrime's XML interface (default port 9000).

    GET  /               -> "TallyPrime Server is Running" (same as real Tally)
    POST /               -> TDL Collection export request -> Collection XML
    POST /_mock/receipt  -> (mock only) record a payment, JSON
                            {"party": "...", "amount": 25000, "invoice": "INV-1050", "narration": "UTR ..."}
    POST /_mock/reset    -> (mock only) rebuild the demo books for today

Run:  python -m revert.mock_tally [--port 9000]
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

from revert.connectors.tally_xml import parse_tally_date, sanitize_xml
from revert.core.money import rupees_to_paise
from revert.core.timeutil import today_ist
from revert.mock_tally import xml_render as R
from revert.mock_tally.dataset import TallyDataset

log = logging.getLogger("mock_tally")


def handle_xml(ds: TallyDataset, body: str, today: date | None = None) -> str:
    """Answer one Tally XML request. Pure function, also used as an in-process transport."""
    try:
        req = ET.fromstring(sanitize_xml(body))
    except ET.ParseError as e:
        return R.error_envelope(f"Could not parse request: {e}")
    if (req.findtext("HEADER/TALLYREQUEST") or "").strip() != "Export":
        return R.error_envelope("Mock Tally is read-only: only Export requests are supported")
    coll = req.find(".//TDL/TDLMESSAGE/COLLECTION")
    if coll is None:
        return R.error_envelope("Only TDL Collection exports are supported by the mock")
    otype = (coll.findtext("TYPE") or "").strip().lower()
    formulae = " ".join((s.text or "") for s in req.iterfind(".//TDL/TDLMESSAGE/SYSTEM"))
    sv = req.find(".//STATICVARIABLES")
    company = (sv.findtext("SVCURRENTCOMPANY") or "").strip() if sv is not None else ""
    if company and company != ds.company.name:
        return R.error_envelope(f"Company '{company}' is not loaded")

    def sv_date(tag: str) -> date | None:
        text = sv.findtext(tag) if sv is not None else None
        return parse_tally_date(text) if text and text.strip() else None

    with ds.lock:
        if otype == "company":
            return R.render_company(ds)
        if otype == "ledger":
            return R.render_ledgers(ds)
        if otype == "bill":
            return R.render_bills(ds)
        if otype == "stockitem":
            return R.render_stock(ds, today or today_ist())
        if otype == "voucher":
            if "IsSales" in formulae:
                return R.render_sales(ds, sv_date("SVFROMDATE"), sv_date("SVTODATE"))
            if "IsReceipt" in formulae:
                return R.render_receipts(ds, sv_date("SVFROMDATE"), sv_date("SVTODATE"))
    return R.error_envelope(f"Could not find Collection of type '{otype}' in the mock")


class _Handler(BaseHTTPRequestHandler):
    server: MockTallyServer

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: str, ctype: str = "text/xml; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, "<RESPONSE>TallyPrime Server is Running</RESPONSE>")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        if self.path == "/_mock/receipt":
            return self._add_receipt(raw)
        if self.path == "/_mock/reset":
            self.server.reset()
            return self._send(200, json.dumps({"ok": True}), "application/json")
        self._send(200, handle_xml(self.server.dataset, raw))

    def _add_receipt(self, raw: str) -> None:
        try:
            p = json.loads(raw or "{}")
            r = self.server.dataset.add_receipt(
                party_name=p["party"],
                amount_paise=rupees_to_paise(p["amount"]),
                on=parse_tally_date(p["date"]) if p.get("date") else today_ist(),
                invoice_number=p.get("invoice"),
                narration=p.get("narration", "Demo payment"),
            )
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, json.dumps({"ok": False, "error": str(e)}), "application/json")
        self._send(200, json.dumps({
            "ok": True,
            "voucher_number": r.voucher_number,
            "allocations": [{"invoice": a.invoice_number, "amount_paise": a.amount_paise} for a in r.allocations],
        }), "application/json")


class MockTallyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], dataset_factory):
        self._factory = dataset_factory
        self.dataset: TallyDataset = dataset_factory()
        super().__init__(addr, _Handler)

    def reset(self) -> None:
        self.dataset = self._factory()


def start_in_thread(dataset_factory, host: str = "127.0.0.1", port: int = 0) -> MockTallyServer:
    """Start a mock server on a background thread (port 0 = any free port). For tests/dev."""
    srv = MockTallyServer((host, port), dataset_factory)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv: list[str] | None = None) -> None:
    from revert.config import get_settings
    from revert.seed.demo_company import build_dataset

    s = get_settings()
    ap = argparse.ArgumentParser(description="Mock TallyPrime XML server")
    ap.add_argument("--host", default=s.mock_tally_host)
    ap.add_argument("--port", type=int, default=s.mock_tally_port)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    srv = MockTallyServer((args.host, args.port), lambda: build_dataset(today_ist()))
    ds = srv.dataset
    print(f"Mock Tally serving '{ds.company.name}' on http://{args.host}:{args.port}")
    print(f"  {len(ds.ledgers)} ledgers, {len(ds.invoices)} invoices, "
          f"{len(ds.receipts)} receipts, {len(ds.stock)} stock items. Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
