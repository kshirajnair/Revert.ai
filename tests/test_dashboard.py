"""Dashboard and chat simulator, through FastAPI's TestClient (real HTTP handling, no server)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from revert.channels.web_simulator import WebSimulatorChannel
from revert.config import get_settings
from revert.core.conversation import receive_message
from revert.core.db import init_db, make_engine, make_session_factory
from revert.core.jobs import sync_job
from revert.core.models import Buyer, MessageLog, ReminderPolicy
from revert.dashboard.app import create_app
from revert.seed.demo_company import MAHADEV, SHARMA
from tests.conftest import AS_OF


@pytest.fixture
def factory(connector):
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        assert sync_job(s, connector, AS_OF).ok
    return factory


@pytest.fixture
def client(factory, connector):
    app = create_app(get_settings(), session_factory=factory, connector=connector,
                     channel=WebSimulatorChannel(), scheduler=False)
    with TestClient(app) as c:
        yield c


def buyer_id(factory, name):
    with factory() as s:
        return s.scalar(select(Buyer.id).where(Buyer.ledger_name == name))


def test_dashboard_lists_overdue_buyers_most_late_first(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Shree Ganesh Electricals" in r.text and "last synced" in r.text
    assert MAHADEV in r.text and SHARMA in r.text
    assert "Recovered this month" in r.text


def test_run_reminders_button_sends_and_shows_in_conversations(client, factory):
    r = client.post("/actions/remind", data={"at": f"{AS_OF.isoformat()}T10:00"})
    assert r.status_code == 200 and "sent" in r.text  # followed the redirect back to the dashboard
    with factory() as s:
        assert s.scalar(select(MessageLog).where(MessageLog.action == "reminder_sent")) is not None
    assert "payment_reminder" in client.get("/").text


def test_run_reminders_at_night_is_deferred(client, factory):
    r = client.post("/actions/remind", data={"at": f"{AS_OF.isoformat()}T22:00"})
    assert "deferred" in r.text and "quiet hours" in r.text
    with factory() as s:
        assert s.scalar(select(MessageLog)) is None


def test_tone_and_pause_controls_save(client, factory):
    bid = buyer_id(factory, MAHADEV)
    assert client.post(f"/buyers/{bid}/tone", data={"tone": "firm"}).status_code == 200
    assert client.post(f"/buyers/{bid}/pause", data={"paused": "on"}).text.count("Paused") == 1
    assert client.post(f"/buyers/{bid}/tone", data={"tone": "rude"}).status_code == 400
    with factory() as s:
        b = s.get(Buyer, bid)
        assert (b.tone, b.paused) == ("firm", True)
    client.post(f"/buyers/{bid}/pause", data={})
    with factory() as s:
        assert s.get(Buyer, bid).paused is False


def test_buyer_page_shows_invoices_and_reminder_history(client, factory):
    client.post("/actions/remind", data={"at": f"{AS_OF.isoformat()}T10:00"})
    r = client.get(f"/buyers/{buyer_id(factory, MAHADEV)}")
    assert r.status_code == 200
    assert "INV-1040" in r.text and "₹22,770" in r.text
    assert "Reminder history" in r.text and "sent" in r.text
    assert client.get("/buyers/999999").status_code == 404


def test_simulator_round_trip_logs_and_hands_off_to_owner(client, factory):
    bid = buyer_id(factory, SHARMA)
    assert client.get(f"/simulator?buyer_id={bid}").status_code == 200

    r = client.post("/simulator/send", data={"buyer_id": bid, "text": "Can you send my statement?"})
    assert r.status_code == 200
    assert "Can you send my statement?" in r.text
    assert "Shree Ganesh Electricals&#39;s assistant" in r.text or "Shree Ganesh Electricals's assistant" in r.text

    with factory() as s:
        inbound = s.scalar(select(MessageLog).where(MessageLog.direction == "in"))
        assert (inbound.intent, inbound.action, inbound.outcome) == ("unclassified", "handoff_owner", "awaiting_owner")
        reply = s.scalar(select(MessageLog).where(MessageLog.direction == "out"))
        assert reply.meta["in_reply_to"] == inbound.id and reply.phone == inbound.phone

    dash = client.get("/").text
    assert "Needs you" in dash and "Can you send my statement?" in dash

    client.post(f"/messages/{inbound.id}/handled")
    with factory() as s:
        assert s.get(MessageLog, inbound.id).outcome == "owner_handled"


def test_owner_reply_goes_to_the_buyer_and_clears_needs_you(client, factory):
    bid = buyer_id(factory, SHARMA)
    client.post("/simulator/send", data={"buyer_id": bid, "text": "hello?"})
    client.post(f"/buyers/{bid}/reply", data={"text": "Will call you in 10 minutes."})
    with factory() as s:
        out = s.scalar(select(MessageLog).where(MessageLog.action == "owner_reply"))
        assert out.body == "Will call you in 10 minutes." and out.buyer_id == bid
        assert s.scalar(select(MessageLog.outcome).where(MessageLog.direction == "in")) == "owner_replied"


def test_unknown_number_is_logged_but_never_answered(factory):
    with factory() as s:
        res = receive_message(s, WebSimulatorChannel(), "+919876543210", "who is this?")
        assert res.buyer is None and res.reply is None
        assert (res.inbound.intent, res.inbound.outcome) == ("unknown_sender", "no_reply")
        assert s.scalar(select(MessageLog).where(MessageLog.direction == "out")) is None


def test_settings_save_and_validate(client, factory):
    assert client.get("/settings").status_code == 200
    r = client.post("/settings", data={"offsets": "0, 7, -3", "quiet_start_hour": 21, "quiet_end_hour": 8,
                                       "max_per_week": 3, "enabled": "on"})
    assert "saved" in r.text
    with factory() as s:
        p = s.scalar(select(ReminderPolicy))
        assert (p.offsets, p.quiet_start_hour, p.quiet_end_hour, p.max_per_week) == ([-3, 0, 7], 21, 8, 3)
    r = client.post("/settings", data={"offsets": "soon", "quiet_start_hour": 20, "quiet_end_hour": 9,
                                       "max_per_week": 2})
    assert "whole numbers" in r.text


def test_sync_button_reports_failure_cleanly(factory):
    from revert.connectors.mock_tally import MockTallyConnector

    dead = MockTallyConnector("http://127.0.0.1:9", timeout_s=1)
    app = create_app(get_settings(), session_factory=factory, connector=dead,
                     channel=WebSimulatorChannel(), scheduler=False)
    with TestClient(app) as c:
        r = c.post("/actions/sync")
    assert "Sync failed" in r.text and "left untouched" in r.text
