"""WhatsApp Cloud API: channel requests, the 24-hour rule, templates, webhook, receipts, media, PDFs.

Meta's Graph API is faked with httpx.MockTransport; nothing touches the network.
"""
import dataclasses
import hashlib
import hmac
import json
from datetime import datetime, time, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from revert.channels.base import ChannelError, OutsideWindow, Template
from revert.channels.web_simulator import WebSimulatorChannel
from revert.channels.whatsapp import WhatsAppChannel, parse_webhook, verify_signature
from revert.config import get_settings
from revert.core.conversation import apply_status
from revert.core.db import init_db, make_engine, make_session_factory
from revert.core.jobs import reminder_job, sync_job
from revert.core.models import Buyer, BuyerPhone, Invoice, MessageLog, PaymentClaim, Reminder
from revert.core.outbox import send_to_buyer
from revert.core.pdf import invoice_pdf
from revert.core.timeutil import IST
from revert.dashboard.app import create_app
from revert.seed.demo_company import DEMO_PREFS, MAHADEV, SHARMA
from tests.conftest import AS_OF

REAL = "+919876543210"  # a real-looking number the owner added for Mahadev
SECRET, VERIFY = "app-secret", "verify-me"
MORNING = datetime.combine(AS_OF, time(11, 0), IST)


class FakeGraph:
    """Records every request; answers like the Graph API."""

    def __init__(self, fail_code: int | None = None):
        self.requests: list[httpx.Request] = []
        self.fail_code = fail_code
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.fail_code and path.endswith("/messages"):
            return httpx.Response(400, json={"error": {"code": self.fail_code, "message": "nope"}})
        if path.endswith("/messages"):
            self.n += 1
            return httpx.Response(200, json={"messages": [{"id": f"wamid.out{self.n}"}]})
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-up-1"})
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(200, content=b"\x89PNG fake", headers={"content-type": "image/png"})
        if "/media-in-" in path:
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/m/1", "mime_type": "image/png"})
        return httpx.Response(200, json={"display_phone_number": "+1 555 0100", "verified_name": "Test"})

    def sent(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/messages")]


def channel(graph: FakeGraph) -> WhatsAppChannel:
    return WhatsAppChannel("tok", "PNID", template_names={"payment_reminder": "revert_payment_reminder",
                                                          "promise_followup": "revert_promise_followup"},
                           transport=httpx.MockTransport(graph))


@pytest.fixture
def factory(connector):
    engine = make_engine("sqlite://")
    init_db(engine)
    f = make_session_factory(engine)
    with f() as s:
        assert sync_job(s, connector, AS_OF).ok
        for b in s.scalars(select(Buyer)):
            b.language, b.tone = DEMO_PREFS[b.ledger_name]["language"], DEMO_PREFS[b.ledger_name]["tone"]
        mahadev = s.scalar(select(Buyer).where(Buyer.ledger_name == MAHADEV))
        for p in mahadev.phones:
            p.is_primary = False
        mahadev.phones.append(BuyerPhone(phone=REAL, source="manual", is_primary=True))
        s.commit()
    return f


def mahadev(s) -> Buyer:
    return s.scalar(select(Buyer).where(Buyer.ledger_name == MAHADEV))


# --- channel --------------------------------------------------------------------------------

def test_send_text_template_and_document_requests():
    g = FakeGraph()
    ch = channel(g)
    assert ch.send_text(REAL, "hello") == "wamid.out1"
    t = Template("payment_reminder", "hi", "x {{1}}", ("Mahadev",))
    ch.send_template(REAL, t)
    ch.send_document(REAL, b"%PDF-1.4", "INV-1040.pdf", "your invoice")
    text_, tmpl, doc = g.sent()
    assert text_["to"] == "919876543210" and text_["text"]["body"] == "hello"
    assert tmpl["template"] == {"name": "revert_payment_reminder", "language": {"code": "hi"},
                                "components": [{"type": "body", "parameters": [{"type": "text", "text": "Mahadev"}]}]}
    assert doc["document"] == {"id": "media-up-1", "filename": "INV-1040.pdf", "caption": "your invoice"}
    assert all(r.headers["authorization"] == "Bearer tok" for r in g.requests)
    assert all("tok" not in str(r.url) for r in g.requests)


def test_meta_errors_become_channel_errors():
    with pytest.raises(OutsideWindow):
        channel(FakeGraph(fail_code=131047)).send_text(REAL, "hi")
    with pytest.raises(ChannelError):
        channel(FakeGraph(fail_code=131026)).send_text(REAL, "hi")


def test_demo_numbers_never_reach_whatsapp():
    ch = channel(FakeGraph())
    assert isinstance(ch.for_phone("+910000000003"), WebSimulatorChannel)
    assert ch.for_phone(REAL) is ch


def test_needs_credentials():
    with pytest.raises(ValueError):
        WhatsAppChannel("", "")


# --- 24-hour window -------------------------------------------------------------------------

def _inbound(s, phone, at):
    s.add(MessageLog(company_id=mahadev(s).company_id, buyer_id=mahadev(s).id, channel="whatsapp", direction="in",
                     phone=phone, body="hi", created_at=at))
    s.commit()


def test_window_closed_sends_template_and_logs_what_the_dealer_saw(factory):
    g = FakeGraph()
    t = Template("payment_reminder", "en", "Hello {{1}}, total {{2}}", ("Mahadev", "₹80,000"))
    with factory() as s:
        msg = send_to_buyer(s, channel(g), mahadev(s), "free text", intent="i", action="a", template=t)
        assert g.sent()[0]["type"] == "template"
        assert msg.body == "Hello Mahadev, total ₹80,000" and msg.meta["template"] == "payment_reminder"
        assert msg.provider_id == "wamid.out1" and msg.delivery == "sent"


def test_window_open_sends_free_text(factory):
    g = FakeGraph()
    with factory() as s:
        _inbound(s, REAL, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2))
        send_to_buyer(s, channel(g), mahadev(s), "free text", intent="i", action="a",
                      template=Template("payment_reminder", "en", "t", ()))
        assert g.sent()[0]["type"] == "text"


def test_window_closed_without_template_refuses(factory):
    with factory() as s:
        _inbound(s, REAL, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25))
        with pytest.raises(OutsideWindow):
            send_to_buyer(s, channel(FakeGraph()), mahadev(s), "free text", intent="i", action="a")


def test_reminders_use_the_hindi_template_on_whatsapp_and_simulator_for_demo_numbers(factory):
    g = FakeGraph()
    with factory() as s:
        report = reminder_job(s, channel(g), MORNING)
        assert MAHADEV in [r.buyer for r in report.sent]
        sent = g.sent()
        assert len(sent) == 1  # only Mahadev has a real number; everyone else went to the simulator
        assert sent[0]["template"]["language"]["code"] == "hi"
        params = [p["text"] for p in sent[0]["template"]["components"][0]["parameters"]]
        assert params[0] == MAHADEV and params[3] == "₹80,000" and "INV-1040 ₹22,770" in params[2]
        assert all("\n" not in p for p in params)
        log = s.scalar(select(MessageLog).where(MessageLog.phone == REAL))
        assert log.channel == "whatsapp" and "Shree Ganesh Electricals का असिस्टेंट" in log.body
        others = s.scalars(select(MessageLog).where(MessageLog.action == "reminder_sent",
                                                    MessageLog.phone != REAL)).all()
        assert others and {m.channel for m in others} == {"web_simulator"}


def test_failed_delivery_reopens_the_reminder_stage(factory):
    g = FakeGraph()
    with factory() as s:
        reminder_job(s, channel(g), MORNING)
        msg = s.scalar(select(MessageLog).where(MessageLog.phone == REAL))
        assert s.scalar(select(Reminder).where(Reminder.message_id == msg.id)) is not None
        apply_status(s, msg.provider_id, "delivered")
        assert msg.delivery == "delivered"
        apply_status(s, msg.provider_id, "sent")  # late, out-of-order receipt doesn't go backwards
        assert msg.delivery == "delivered"
        apply_status(s, msg.provider_id, "failed", [{"code": 131026, "title": "undeliverable"}])
        assert (msg.delivery, msg.outcome) == ("failed", "failed")
        assert s.scalar(select(Reminder).where(Reminder.message_id == msg.id)) is None
        report = reminder_job(s, channel(g), MORNING + timedelta(hours=1))
        assert MAHADEV in [r.buyer for r in report.sent]


# --- webhook --------------------------------------------------------------------------------

@pytest.fixture
def wa(factory, connector, tmp_path):
    g = FakeGraph()
    settings = dataclasses.replace(get_settings(), channel="whatsapp", whatsapp_app_secret=SECRET,
                                   whatsapp_verify_token=VERIFY, media_dir=str(tmp_path / "media"))
    app = create_app(settings, session_factory=factory, connector=connector, channel=channel(g), scheduler=False)
    with TestClient(app) as c:
        yield c, g


def post(client, payload, secret=SECRET):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/whatsapp", content=raw,
                       headers={"content-type": "application/json", "x-hub-signature-256": sig})


def envelope(messages=(), statuses=()):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "PNID"},
        "messages": list(messages), "statuses": list(statuses)}}]}]}


def text_msg(body, wamid="wamid.in1", sender="919876543210"):
    return {"from": sender, "id": wamid, "timestamp": "1793500000", "type": "text", "text": {"body": body}}


def test_webhook_verification(wa):
    c, _ = wa
    ok = c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                             "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                               "hub.challenge": "1"}).status_code == 403


def test_webhook_rejects_bad_signatures(wa, factory):
    c, g = wa
    assert post(c, envelope([text_msg("hi")]), secret="wrong").status_code == 403
    assert c.post("/webhooks/whatsapp", json=envelope([text_msg("hi")])).status_code == 403
    with factory() as s:
        assert s.scalar(select(MessageLog)) is None


def test_dealer_message_gets_an_agent_reply_on_whatsapp_once(wa, factory):
    c, g = wa
    assert post(c, envelope([text_msg("how much do I owe?")])).json() == {"ok": True}
    assert post(c, envelope([text_msg("how much do I owe?")])).status_code == 200  # Meta retry
    sent = g.sent()
    assert len(sent) == 1 and sent[0]["type"] == "text" and "₹80,000" in sent[0]["text"]["body"]
    with factory() as s:
        inbound = s.scalars(select(MessageLog).where(MessageLog.direction == "in")).all()
        assert len(inbound) == 1 and inbound[0].provider_id == "wamid.in1"
        assert (inbound[0].channel, inbound[0].intent) == ("whatsapp", "balance_query")


def test_statement_request_sends_text_then_pdf(wa, factory):
    c, g = wa
    post(c, envelope([text_msg("please send my statement")]))
    kinds = [m["type"] for m in g.sent()]
    assert kinds == ["text", "document"]
    upload = next(r for r in g.requests if r.url.path.endswith("/media"))
    assert b"%PDF-1.4" in upload.content
    with factory() as s:
        doc = s.scalar(select(MessageLog).where(MessageLog.action == "send_document"))
        assert doc.body.startswith("📄 Statement-") and doc.meta["attachment"] == {"kind": "statement"}


def test_payment_screenshot_is_saved_and_flagged(wa, factory, tmp_path):
    c, g = wa
    img = {"from": "919876543210", "id": "wamid.img1", "timestamp": "1", "type": "image",
           "image": {"id": "media-in-1", "mime_type": "image/png", "caption": ""}}
    post(c, envelope([img]))
    with factory() as s:
        m = s.scalar(select(MessageLog).where(MessageLog.provider_id == "wamid.img1"))
        assert (m.intent, m.action, m.outcome) == ("already_paid", "log_payment_claim", "awaiting_owner")
        assert m.meta["media"]["path"].endswith(".png")
        assert s.scalar(select(PaymentClaim)).reference == "screenshot"
        mid = m.id
    assert "स्क्रीनशॉट" in g.sent()[0]["text"]["body"]  # Mahadev prefers Hindi
    r = c.get(f"/messages/{mid}/media")
    assert r.status_code == 200 and r.content == b"\x89PNG fake"


def test_voice_note_goes_to_owner(wa, factory):
    c, g = wa
    post(c, envelope([{"from": "919876543210", "id": "wamid.a1", "timestamp": "1", "type": "audio",
                       "audio": {"id": "media-in-2", "mime_type": "audio/ogg"}}]))
    with factory() as s:
        m = s.scalar(select(MessageLog).where(MessageLog.provider_id == "wamid.a1"))
        assert (m.intent, m.outcome) == ("media", "awaiting_owner")


def test_unknown_sender_is_logged_not_answered(wa, factory):
    c, g = wa
    post(c, envelope([text_msg("hello", sender="919999999999")]))
    assert g.sent() == []
    with factory() as s:
        assert s.scalar(select(MessageLog)).intent == "unknown_sender"


def test_status_webhook_updates_delivery(wa, factory):
    c, g = wa
    post(c, envelope([text_msg("hi")]))
    post(c, envelope(statuses=[{"id": "wamid.out1", "status": "read", "recipient_id": "919876543210"}]))
    with factory() as s:
        assert s.scalar(select(MessageLog).where(MessageLog.provider_id == "wamid.out1")).delivery == "read"


def test_webhook_not_configured_is_503(factory, connector):
    app = create_app(get_settings(), session_factory=factory, connector=connector,
                     channel=WebSimulatorChannel(), scheduler=False)
    with TestClient(app) as c:
        assert c.post("/webhooks/whatsapp", json={}).status_code == 503


def test_parse_webhook_buttons_and_statuses():
    msgs, sts = parse_webhook(envelope(
        [{"from": "919876543210", "id": "w1", "type": "button", "button": {"text": "Paid"}},
         {"from": "919876543210", "id": "w2", "type": "interactive",
          "interactive": {"button_reply": {"title": "Send statement"}}}],
        [{"id": "w3", "status": "failed", "errors": [{"code": 1}]}]))
    assert [(m.kind, m.text, m.phone) for m in msgs] == [("text", "Paid", REAL), ("text", "Send statement", REAL)]
    assert sts[0].status == "failed" and sts[0].errors == [{"code": 1}]
    assert verify_signature("k", b"x", "sha256=" + hmac.new(b"k", b"x", hashlib.sha256).hexdigest())
    assert not verify_signature("", b"x", "sha256=00")


# --- dashboard: numbers, PDFs, owner reply window --------------------------------------------

def test_add_and_remove_numbers(wa, factory):
    c, _ = wa
    with factory() as s:
        sid = s.scalar(select(Buyer.id).where(Buyer.ledger_name == SHARMA))
    assert "Saved +918888877777" in c.post(f"/buyers/{sid}/phones", data={"phone": "88888 77777", "primary": "on"}).text
    assert "already belongs to Mahadev" in c.post(f"/buyers/{sid}/phones", data={"phone": REAL}).text
    assert "look like a phone number" in c.post(f"/buyers/{sid}/phones", data={"phone": "12"}).text
    with factory() as s:
        b = s.get(Buyer, sid)
        assert b.primary_phone == "+918888877777"
        tally = next(p for p in b.phones if p.source == "tally")
        manual = next(p for p in b.phones if p.source == "manual")
    assert "only be changed in Tally" in c.post(f"/buyers/{sid}/phones/{tally.id}/delete").text
    c.post(f"/buyers/{sid}/phones/{manual.id}/delete")
    with factory() as s:
        assert s.get(Buyer, sid).primary_phone == tally.phone


def test_owner_reply_outside_window_is_explained(wa, factory):
    c, g = wa
    with factory() as s:
        mid = mahadev(s).id
    r = c.post(f"/buyers/{mid}/reply", data={"text": "please call me"})
    assert "Not sent" in r.text and "24 hours" in r.text and g.sent() == []


def test_pdfs_from_the_dashboard(wa, factory):
    c, _ = wa
    with factory() as s:
        m = mahadev(s)
        inv = s.scalar(select(Invoice).where(Invoice.number == "INV-1040"))
        other = s.scalar(select(Invoice).where(Invoice.buyer_id != m.id))
        mid, iid, oid = m.id, inv.id, other.id
    r = c.get(f"/buyers/{mid}/invoices/{iid}.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and b"Rs. 22,770.00" in r.content
    assert c.get(f"/buyers/{mid}/invoices/{oid}.pdf").status_code == 404  # not this buyer's invoice
    assert b"Rs. 80,000.00" in c.get(f"/buyers/{mid}/statement.pdf").content


def test_invoice_pdf_is_well_formed(factory):
    with factory() as s:
        inv = s.scalar(select(Invoice).where(Invoice.number == "INV-1040"))
        pdf = invoice_pdf(inv.buyer.company, inv.buyer, inv, "now")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    xref = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[xref:xref + 4] == b"xref"
    for line in inv.lines:
        assert line.item_name.split(" (")[0].encode() in pdf


def test_old_database_gets_new_columns(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    raw = create_engine(url)
    with raw.begin() as conn:
        conn.execute(text("CREATE TABLE message_log (id INTEGER PRIMARY KEY, company_id INTEGER, buyer_id INTEGER, "
                          "channel VARCHAR(20), direction VARCHAR(3), phone VARCHAR(20), body TEXT, intent VARCHAR(40),"
                          " action VARCHAR(80), outcome VARCHAR(80), meta JSON, created_at DATETIME)"))
    init_db(make_engine(url))
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("message_log")}
    assert {"provider_id", "delivery"} <= cols


def test_dashboard_password_protects_everything_but_the_webhook(factory, connector):
    import base64

    settings = dataclasses.replace(get_settings(), dashboard_password="pw", whatsapp_verify_token=VERIFY)
    app = create_app(settings, session_factory=factory, connector=connector, channel=WebSimulatorChannel(),
                     scheduler=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 401 and c.get("/simulator").status_code == 401
        good = {"authorization": "Basic " + base64.b64encode(b"owner:pw").decode()}
        bad = {"authorization": "Basic " + base64.b64encode(b"owner:nope").decode()}
        assert c.get("/", headers=good).status_code == 200
        assert c.get("/", headers=bad).status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                                   "hub.challenge": "7"}).text == "7"
