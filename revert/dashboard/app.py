"""Owner dashboard + web chat simulator (FastAPI + Jinja2 + HTMX).

Run:  python -m revert serve        then open http://127.0.0.1:8000

Pages are server-rendered HTML. HTMX (one script tag) swaps small fragments for the tone and
pause controls and for the simulator's chat thread, so there is no frontend build step.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from revert.channels import get_channel
from revert.channels.base import Channel
from revert.config import Settings, get_settings
from revert.connectors import get_connector
from revert.connectors.base import Connector
from revert.core.ageing import buyer_ageing
from revert.core.conversation import receive_message
from revert.core.db import init_db, make_engine, make_session_factory
from revert.core.jobs import reminder_job, sync_job
from revert.core.models import TONES, Buyer, Company, Dispute, MessageLog, PaymentClaim, Reminder
from revert.core.money import format_inr, format_inr_exact
from revert.core.outbox import send_to_buyer
from revert.core.reminders import get_policy, in_quiet_hours
from revert.core.timeutil import IST, format_ist, now_ist, to_ist, today_ist, utcnow
from revert.dashboard import queries
from revert.verticals.collections.handler import default_agent

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["inr"] = format_inr
templates.env.filters["inr_exact"] = format_inr_exact
templates.env.filters["ist"] = format_ist
templates.env.filters["ist_short"] = lambda dt: to_ist(dt).strftime("%d %b, %I:%M %p") if dt else "-"


def create_app(settings: Settings | None = None, session_factory: sessionmaker | None = None,
               connector: Connector | None = None, channel: Channel | None = None,
               offline: bool = False, scheduler: bool = True, agent=None) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    connector = connector or get_connector(settings, offline=offline)
    channel = channel or get_channel(settings.channel)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = _start_scheduler(settings, session_factory, connector, channel) if scheduler else None
        yield
        if sched:
            sched.shutdown(wait=False)

    app = FastAPI(title="Revert", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.connector = connector
    app.state.channel = channel
    app.state.agent = agent or default_agent()
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    _routes(app)
    return app


def _start_scheduler(settings: Settings, session_factory, connector: Connector, channel: Channel):
    from apscheduler.schedulers.background import BackgroundScheduler

    def run_sync():
        with session_factory() as s:
            res = sync_job(s, connector)
            if not res.ok:
                log.warning("scheduled sync failed: %s", res.error)

    def run_reminders():
        with session_factory() as s:
            reminder_job(s, channel)

    sched = BackgroundScheduler(timezone=IST)
    if settings.sync_every_min > 0:
        sched.add_job(run_sync, "interval", minutes=settings.sync_every_min, id="sync", max_instances=1)
    if settings.remind_every_min > 0:
        sched.add_job(run_reminders, "interval", minutes=settings.remind_every_min, id="remind", max_instances=1)
    sched.start()
    log.info("scheduler: sync every %s min, reminders every %s min", settings.sync_every_min, settings.remind_every_min)
    return sched


def get_db(request: Request):
    s = request.app.state.session_factory()
    try:
        yield s
    finally:
        s.close()


def _company(session: Session) -> Company | None:
    return session.scalar(select(Company).order_by(Company.id))


def _buyer(session: Session, buyer_id: int) -> Buyer:
    b = session.get(Buyer, buyer_id)
    if b is None:
        raise HTTPException(404, "No such buyer")
    return b


def _back(url: str, msg: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}msg={quote(msg)}" if msg else url, status_code=303)


def _parse_at(at: str) -> datetime | None:
    """Value from <input type=datetime-local>, read as IST. Empty means now."""
    if not at.strip():
        return None
    return datetime.fromisoformat(at.strip()).replace(tzinfo=IST)


def _routes(app: FastAPI) -> None:
    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"msg": request.query_params.get("msg", ""), **ctx})

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, db: Session = Depends(get_db)):
        company = _company(db)
        if company is None:
            return page(request, "empty.html")
        as_of = today_ist()
        policy = get_policy(db, company.id)
        db.commit()
        rows = queries.overdue_rows(db, company.id, as_of)
        return page(
            request, "dashboard.html", company=company, as_of=as_of, rows=rows, tones=TONES,
            total_overdue=sum(r.ageing.total_overdue_paise for r in rows),
            month=queries.month_totals(db, company.id, as_of),
            needs=queries.needs_owner(db, company.id),
            convos=queries.recent_conversations(db, company.id),
            quiet_now=in_quiet_hours(now_ist(), policy), policy=policy,
            promises=queries.promises_due(db, company.id, as_of),
            upcoming=queries.upcoming_promises(db, company.id, as_of),
            disputes=queries.open_disputes(db, company.id),
            engine=request.app.state.agent.engine,
        )

    @app.post("/actions/sync")
    def action_sync(request: Request, db: Session = Depends(get_db)):
        res = sync_job(db, request.app.state.connector)
        if not res.ok:
            return _back("/", f"Sync failed: {res.error} Existing data was left untouched.")
        paid = f" Payments received against {', '.join(res.paid_invoices)}; their reminders are stopped." \
            if res.paid_invoices else ""
        return _back("/", f"Synced {res.counts['invoices']} invoices.{paid}")

    @app.post("/actions/remind")
    def action_remind(request: Request, at: str = Form(""), db: Session = Depends(get_db)):
        report = reminder_job(db, request.app.state.channel, _parse_at(at))
        details = report.reasons()
        when = f" (as if at {report.at:%d %b %I:%M %p} IST)" if at.strip() else ""
        followed = f" Followed up on broken promises: {', '.join(report.followups)}." if report.followups else ""
        return _back("/", f"Reminder run{when}: {report.summary()}." + (f" {details}." if details else "") + followed)

    @app.get("/buyers/{buyer_id}", response_class=HTMLResponse)
    def buyer_page(request: Request, buyer_id: int, db: Session = Depends(get_db)):
        buyer = db.scalar(select(Buyer).where(Buyer.id == buyer_id)
                          .options(selectinload(Buyer.invoices), selectinload(Buyer.phones)))
        if buyer is None:
            raise HTTPException(404, "No such buyer")
        reminders = db.scalars(select(Reminder).where(Reminder.buyer_id == buyer_id)
                               .options(selectinload(Reminder.invoice)).order_by(Reminder.id.desc())).all()
        return page(request, "buyer.html", buyer=buyer, company=buyer.company, tones=TONES,
                    ageing=buyer_ageing(buyer, today_ist()), reminders=reminders,
                    messages=queries.thread(db, buyer_id), **queries.buyer_records(db, buyer_id))

    @app.post("/buyers/{buyer_id}/tone", response_class=HTMLResponse)
    def set_tone(buyer_id: int, tone: str = Form(...), db: Session = Depends(get_db)):
        if tone not in TONES:
            raise HTTPException(400, "Unknown tone")
        _buyer(db, buyer_id).tone = tone
        db.commit()
        return HTMLResponse('<span class="saved">Saved</span>')

    @app.post("/buyers/{buyer_id}/pause", response_class=HTMLResponse)
    def set_pause(buyer_id: int, paused: str = Form(""), db: Session = Depends(get_db)):
        b = _buyer(db, buyer_id)
        b.paused = paused == "on"
        db.commit()
        return HTMLResponse(f'<span class="saved">{"Paused" if b.paused else "Active"}</span>')

    @app.post("/buyers/{buyer_id}/reply")
    def owner_reply(request: Request, buyer_id: int, text: str = Form(...), db: Session = Depends(get_db)):
        b = _buyer(db, buyer_id)
        if text.strip():
            send_to_buyer(db, request.app.state.channel, b, text.strip(), intent="owner_message", action="owner_reply")
            for m in queries.needs_owner(db, b.company_id):
                if m.buyer_id == b.id:
                    m.outcome = "owner_replied"
            db.commit()
        return _back(f"/buyers/{buyer_id}", "Reply sent.")

    @app.post("/messages/{message_id}/handled")
    def mark_handled(message_id: int, db: Session = Depends(get_db)):
        m = db.get(MessageLog, message_id)
        if m is None:
            raise HTTPException(404, "No such message")
        m.outcome = "owner_handled"
        db.commit()
        return _back("/", "Marked as handled.")

    @app.post("/disputes/{dispute_id}/resolve")
    def resolve_dispute(request: Request, dispute_id: int, back: str = Form("/"), db: Session = Depends(get_db)):
        d = db.get(Dispute, dispute_id)
        if d is None:
            raise HTTPException(404, "No such dispute")
        d.status, d.resolved_at = "resolved", utcnow()
        db.commit()
        return _back(back if back.startswith("/") else "/", "Dispute resolved. Reminders resume on the normal schedule.")

    @app.post("/claims/{claim_id}/close")
    def close_claim(request: Request, claim_id: int, back: str = Form("/"), db: Session = Depends(get_db)):
        c = db.get(PaymentClaim, claim_id)
        if c is None:
            raise HTTPException(404, "No such claim")
        c.status = "closed"
        db.commit()
        return _back(back if back.startswith("/") else "/", "Payment claim closed.")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, db: Session = Depends(get_db)):
        company = _company(db)
        if company is None:
            return page(request, "empty.html")
        policy = get_policy(db, company.id)
        db.commit()
        return page(request, "settings.html", company=company, policy=policy)

    @app.post("/settings")
    def save_settings(offsets: str = Form(...), quiet_start_hour: int = Form(...), quiet_end_hour: int = Form(...),
                      max_per_week: int = Form(...), enabled: str = Form(""), db: Session = Depends(get_db)):
        company = _company(db)
        if company is None:
            return _back("/settings")
        try:
            parsed = sorted({int(x) for x in offsets.replace(" ", "").split(",") if x})
        except ValueError:
            return _back("/settings", "Stages must be whole numbers separated by commas, e.g. -3, 0, 7, 15, 30")
        if not parsed or not all(0 <= h <= 23 for h in (quiet_start_hour, quiet_end_hour)) or max_per_week < 1:
            return _back("/settings", "Please check the values: at least one stage, hours 0-23, limit at least 1.")
        policy = get_policy(db, company.id)
        policy.offsets, policy.enabled = parsed, enabled == "on"
        policy.quiet_start_hour, policy.quiet_end_hour, policy.max_per_week = quiet_start_hour, quiet_end_hour, max_per_week
        db.commit()
        return _back("/settings", "Reminder schedule saved.")

    # --- web chat simulator -------------------------------------------------------------

    @app.get("/simulator", response_class=HTMLResponse)
    def simulator(request: Request, buyer_id: int | None = None, db: Session = Depends(get_db)):
        company = _company(db)
        if company is None:
            return page(request, "empty.html")
        buyers = db.scalars(select(Buyer).where(Buyer.company_id == company.id)
                            .options(selectinload(Buyer.phones)).order_by(Buyer.ledger_name)).all()
        current = next((b for b in buyers if b.id == buyer_id), buyers[0] if buyers else None)
        return page(request, "simulator.html", company=company, buyers=buyers, current=current,
                    messages=queries.thread(db, current.id) if current else [])

    @app.get("/simulator/thread", response_class=HTMLResponse)
    def simulator_thread(request: Request, buyer_id: int, db: Session = Depends(get_db)):
        b = _buyer(db, buyer_id)
        return page(request, "_thread.html", current=b, company=b.company, messages=queries.thread(db, buyer_id))

    @app.post("/simulator/send", response_class=HTMLResponse)
    def simulator_send(request: Request, buyer_id: int = Form(...), text: str = Form(...),
                       db: Session = Depends(get_db)):
        b = _buyer(db, buyer_id)
        if text.strip():
            receive_message(db, request.app.state.channel, b.primary_phone or "", text.strip(),
                            agent=request.app.state.agent)
        return page(request, "_thread.html", current=b, company=b.company, messages=queries.thread(db, buyer_id))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}
