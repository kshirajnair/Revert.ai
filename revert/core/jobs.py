"""Scheduled jobs, shared by the CLI, the dashboard buttons and APScheduler."""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from revert.channels.base import Channel
from revert.connectors.base import Connector
from revert.core.reminders import RunReport, on_payments_synced, run_reminders
from revert.core.sync import SyncResult, run_sync
from revert.core.timeutil import today_ist

log = logging.getLogger(__name__)


def sync_job(session: Session, connector: Connector, as_of: date | None = None) -> SyncResult:
    """Sync, then stop reminders for anything that just got paid."""
    as_of = as_of or today_ist()
    res = run_sync(session, connector, as_of=as_of)
    if res.ok and res.paid_invoices:
        n = on_payments_synced(session, res.company_id, res.paid_invoices, as_of)
        log.info("payments synced for %s; %d reminder stage(s) cancelled", res.paid_invoices, n)
    return res


def reminder_job(session: Session, channel: Channel, now: datetime | None = None) -> RunReport:
    report = run_reminders(session, channel, now)
    log.info("reminder run at %s: %s", report.at, report.summary())
    return report
