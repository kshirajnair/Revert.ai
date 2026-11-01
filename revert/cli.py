"""Command line entry point: python -m revert <command>

    seed    [--offline]           reset DB, sync from Tally (mock), apply demo prefs (tone/language)
    sync    [--offline]           pull latest data from the configured connector
    ageing  [--buyer NAME]        print the ageing report
    status                        last sync + row counts
    pay     PARTY AMOUNT [--invoice INV-xxxx]   (mock only) record a payment in mock Tally
    remind  [--at "YYYY-MM-DD HH:MM"]           run the reminder engine once (optionally as if at an IST time)
    serve   [--offline] [--no-scheduler]        dashboard + chat simulator on http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import func, select

from revert.config import get_settings
from revert.connectors import get_connector
from revert.core.ageing import Bucket, ageing_report
from revert.core.db import init_db, make_engine, make_session_factory
from revert.core.models import Buyer, Company, Invoice, Receipt, StockItem, SyncRun
from revert.core.money import format_inr
from revert.core.jobs import reminder_job, sync_job
from revert.core.timeutil import IST, format_ist, today_ist


def _session(reset: bool = False):
    s = get_settings()
    engine = make_engine(s.database_url)
    init_db(engine, reset=reset)
    return make_session_factory(engine)()


def _company(session) -> Company:
    c = session.scalar(select(Company))
    if c is None:
        sys.exit("No data yet. Run: python -m revert seed")
    return c


def cmd_sync(args, reset: bool = False) -> int:
    settings = get_settings()
    session = _session(reset=reset)
    res = sync_job(session, get_connector(settings, offline=args.offline))
    if not res.ok:
        print(f"Sync FAILED: {res.error}")
        print("Existing data was left untouched.")
        return 1
    print(f"Sync OK: {res.counts}")
    if res.paid_invoices:
        print(f"Payments received against: {', '.join(res.paid_invoices)} (their reminders are stopped)")
    return 0


def cmd_seed(args) -> int:
    from revert.seed.demo_company import DEMO_PREFS

    rc = cmd_sync(args, reset=True)
    if rc:
        if not args.offline:
            print("Start the mock first:  python -m revert.mock_tally   (or use: seed --offline)")
        return rc
    session = _session()
    for b in session.scalars(select(Buyer)):
        prefs = DEMO_PREFS.get(b.ledger_name)
        if prefs:
            b.language, b.tone = prefs["language"], prefs["tone"]
    session.commit()
    print("Demo preferences (language, tone) applied.")
    return 0


def cmd_ageing(args) -> int:
    session = _session()
    company = _company(session)
    as_of = today_ist()
    rows = ageing_report(session, company.id, as_of)
    if args.buyer:
        rows = [r for r in rows if args.buyer.lower() in r.name.lower()]
    print(f"{company.name} - ageing as of {as_of:%d %b %Y} (data synced {format_ist(company.last_synced_at)})\n")
    cols = [Bucket.NOT_DUE, Bucket.D0_30, Bucket.D31_60, Bucket.D61_90, Bucket.D90_PLUS]
    hdr = f"{'Buyer':<30}{'Outstanding':>13}" + "".join(f"{c.value:>12}" for c in cols) + f"{'Oldest':>8}{'Limit':>12}"
    print(hdr)
    print("-" * len(hdr))
    totals = {c: 0 for c in cols}
    for r in rows:
        limit = format_inr(r.credit_limit_paise) if r.credit_limit_paise else "-"
        print(f"{r.name[:29]:<30}{format_inr(r.total_outstanding_paise):>13}"
              + "".join(f"{format_inr(r.buckets[c]) if r.buckets[c] else '-':>12}" for c in cols)
              + f"{(str(r.oldest_days_overdue) + 'd') if r.oldest_days_overdue >= 0 else '-':>8}{limit:>12}")
        for c in cols:
            totals[c] += r.buckets[c]
    print("-" * len(hdr))
    print(f"{'TOTAL':<30}{format_inr(sum(totals.values())):>13}"
          + "".join(f"{format_inr(totals[c]):>12}" for c in cols))
    if args.buyer and rows:
        for r in rows:
            print(f"\n{r.name}:")
            for i in r.invoices:
                state = f"{i.days_overdue}d overdue" if i.days_overdue >= 0 else f"due in {-i.days_overdue}d"
                print(f"  {i.number}  {i.date:%d %b}  due {i.due_date:%d %b}  "
                      f"total {format_inr(i.total_paise):>10}  pending {format_inr(i.balance_paise):>10}  {state}")
    return 0


def cmd_status(_args) -> int:
    session = _session()
    company = _company(session)
    last = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    print(f"Company:      {company.name}")
    print(f"Last synced:  {format_ist(company.last_synced_at)}")
    if last:
        print(f"Last run:     #{last.id} {last.status} via {last.source} {last.error}")
    for label, model in (("Buyers", Buyer), ("Invoices", Invoice), ("Receipts", Receipt), ("Stock items", StockItem)):
        print(f"{label + ':':<14}{session.scalar(select(func.count()).select_from(model))}")
    return 0


def cmd_pay(args) -> int:
    from revert.connectors.mock_tally import MockTallyConnector

    s = get_settings()
    if s.connector != "mock_tally":
        sys.exit("`pay` only works against the mock Tally (never writes to real Tally)")
    res = MockTallyConnector(s.tally_url, timeout_s=s.tally_timeout_s).record_payment(
        args.party, args.amount, args.invoice)
    print(f"Mock Tally recorded {res['voucher_number']}. Run `python -m revert sync` to pick it up.")
    return 0


def cmd_remind(args) -> int:
    from revert.channels import get_channel

    session = _session()
    _company(session)
    at = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=IST) if args.at else None
    report = reminder_job(session, get_channel(get_settings().channel), at)
    print(f"Reminder run at {report.at:%d %b %Y %I:%M %p} IST: {report.summary()}")
    for r in report.results:
        print(f"  {r.status:<9}{r.buyer:<30}{', '.join(r.invoices)}{'  - ' + r.reason if r.reason else ''}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from revert.dashboard.app import create_app

    s = get_settings()
    app = create_app(s, offline=args.offline, scheduler=not args.no_scheduler)
    print(f"Dashboard:       http://{s.host}:{s.port}/")
    print(f"Chat simulator:  http://{s.host}:{s.port}/simulator")
    uvicorn.run(app, host=s.host, port=s.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # ₹ on Windows consoles
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m revert")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "sync"):
        p = sub.add_parser(name)
        p.add_argument("--offline", action="store_true", help="serve demo books in-process, no mock server needed")
    p = sub.add_parser("ageing")
    p.add_argument("--buyer")
    sub.add_parser("status")
    p = sub.add_parser("pay")
    p.add_argument("party")
    p.add_argument("amount", type=float, help="rupees")
    p.add_argument("--invoice")
    p = sub.add_parser("remind")
    p.add_argument("--at", help='pretend it is this IST time, e.g. "2026-11-02 10:30" (demo/testing)')
    p = sub.add_parser("serve")
    p.add_argument("--offline", action="store_true", help="Sync now serves demo books in-process, no mock server needed")
    p.add_argument("--no-scheduler", action="store_true", help="don't run sync/reminders in the background")
    args = ap.parse_args(argv)
    return {"seed": cmd_seed, "sync": cmd_sync, "ageing": cmd_ageing, "status": cmd_status, "pay": cmd_pay,
            "remind": cmd_remind, "serve": cmd_serve}[args.cmd](args)
