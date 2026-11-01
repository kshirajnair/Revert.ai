"""The collections agent: understand -> call tools -> compose reply -> guard.

1. `understand` (LLM or offline rules) gives an intent and checked slots.
2. One handler per intent calls tools (`tools.py`) that read or write the DB.
3. The reply is composed from fixed templates in the dealer's language, filled only with
   values those tools returned.
4. `guard.check_reply` verifies every number, the wording and the signature. If anything fails,
   the dealer gets a plain hand-off message and the owner sees it under "Needs you".

Nothing here messages anyone: the caller (core/conversation.py) sends the reply via the outbox.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from revert.agent import guard, parse, tools
from revert.agent.llm import LLMProvider
from revert.agent.tools import Ctx, day, money, qty_text, receipt_ref
from revert.agent.understand import Understanding, understand
from revert.core.models import Buyer, Invoice, MessageLog
from revert.core.timeutil import to_ist, today_ist
from revert.verticals.collections.replies import Composer

log = logging.getLogger(__name__)

RECENT_PAYMENT_DAYS = 14  # "I already paid" with no amount: anything received this recently counts
MATCH_PAYMENT_DAYS = 30  # "I paid 20k": look this far back for a receipt of that amount

# outcomes on the inbound message
ANSWERED, AWAITING_OWNER, AWAITING_BUYER = "answered", "awaiting_owner", "awaiting_buyer"


@dataclass
class Reply:
    body: str
    intent: str  # what the buyer wanted
    action: str  # what Revert did about it
    outcome: str  # state of the inbound message afterwards
    meta: dict = field(default_factory=dict)
    # PDFs to send after the text: {"kind": "invoice", "invoice_id": n} | {"kind": "statement"}
    attachments: list[dict] = field(default_factory=list)


class Agent:
    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    @property
    def engine(self) -> str:
        return f"{self.llm.name} ({self.llm.model})" if self.llm else "offline rules"

    def handle(self, session: Session, buyer: Buyer, inbound: MessageLog, today: date | None = None,
               now_utc: datetime | None = None) -> Reply:
        today = today or (to_ist(now_utc).date() if now_utc else today_ist())
        media = (inbound.meta or {}).get("media")
        text = media.get("caption", "") if media else inbound.body
        u = understand(text, today, buyer.language, self.llm)
        ctx = Ctx(session, buyer.company, buyer, today, now_utc=now_utc)
        c = Composer(u.language, buyer.company.name, buyer.ledger_name, upset=u.upset)
        if media and media.get("kind") not in ("image", "document"):
            reply = Reply(c.say("unsupported_media").text(), "media", "handoff_owner", AWAITING_OWNER)
        elif media and u.intent in ("already_paid", "unclear", "smalltalk", "promise_to_pay"):
            # a picture or PDF from a dealer being chased is almost always a payment proof
            reply = self._screenshot(ctx, u, c, inbound)
        else:
            reply = getattr(self, f"_{u.intent}")(ctx, u, c, inbound)
        if u.upset and reply.outcome == ANSWERED:
            reply.outcome = AWAITING_OWNER  # an annoyed dealer gets a person's attention too

        problems = guard.check_reply(reply.body, ctx.facts.texts, inbound.body, buyer.company.name, c.lang)
        if problems:
            log.error("guard blocked reply to %s: %s\n%s", buyer.ledger_name, problems, reply.body)
            ctx.rollback()  # undo anything the handler wrote; the owner takes over
            blocked = Composer(u.language, buyer.company.name, buyer.ledger_name)
            reply = Reply(blocked.say("handoff").text(), u.intent, "guard_blocked", AWAITING_OWNER)
        reply.meta = {"understanding": u.as_meta(), "guard": problems, **reply.meta}
        return reply

    # --- intents ------------------------------------------------------------------------------

    def _already_paid(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        found = []
        if u.payment_ref:
            r = tools.receipt_by_reference(ctx, u.payment_ref)
            found = [r] if r else []
        else:
            amounts = {f.value for f in parse.find_amounts(inbound.body, ctx.today)}
            if amounts:
                recent = tools.recent_receipts(ctx, MATCH_PAYMENT_DAYS)
                found = [r for r in recent if any(abs(r.amount_paise - a) <= 100 for a in amounts)]
            else:
                found = tools.recent_receipts(ctx, RECENT_PAYMENT_DAYS)

        if found:
            for r in found:
                tools.close_claims_matched_by(ctx, r)
            c.say("paid_found")
            for r in found[:3]:
                c.say("receipt_line", amount=money(r.amount_paise), date=day(r.date), ref=receipt_ref(r))
            self._pending_line(ctx, c)
            return Reply(c.text(), u.intent, "check_receipts", ANSWERED)

        tools.log_claim(ctx, u.payment_ref, inbound)
        if u.payment_ref:
            c.say("ref_not_found", ref=u.payment_ref)
        else:
            c.say("paid_not_found")
        return Reply(c.text(), u.intent, "log_payment_claim", AWAITING_OWNER)

    def _screenshot(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        tools.log_claim(ctx, u.payment_ref or "screenshot", inbound)
        c.say("screenshot_received")
        return Reply(c.text(), "already_paid", "log_payment_claim", AWAITING_OWNER)

    def _pending_line(self, ctx: Ctx, c: Composer) -> None:
        dues = tools.open_dues(ctx)
        if dues.total_paise:
            c.say("still_pending", total=money(dues.total_paise))
        else:
            c.say("nothing_pending")
        c.synced(tools.synced_at(ctx))

    def _promise_to_pay(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        dues = tools.open_dues(ctx)
        if not dues.total_paise:
            c.say("no_dues").synced(tools.synced_at(ctx))
            return Reply(c.text(), u.intent, "check_dues", ANSWERED)
        inst = list(u.installments)
        if not inst or any(i.due is None for i in inst):
            c.say("promise_need_date")
            return Reply(c.text(), u.intent, "ask_date", AWAITING_BUYER)
        rows = tools.log_promise(ctx, inst, dues.total_paise, inbound)
        if not rows:
            c.say("promise_need_date")
            return Reply(c.text(), u.intent, "ask_date", AWAITING_BUYER)
        c.say("promise_logged")
        for p in rows:
            c.say(f"promise_{p.kind}", amount=money(p.amount_paise), date=day(p.due_date))
        c.say("promise_hold").say("total", total=money(dues.total_paise)).synced(tools.synced_at(ctx))
        return Reply(c.text(), u.intent, "log_promise", "promise_logged",
                     meta={"promise_ids": [p.id for p in rows]})

    def _dispute(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        invoices = tools.find_invoices(ctx, u.invoice_refs)
        inv: Invoice | None = invoices[0] if invoices else None
        d = tools.log_dispute(ctx, inv, inbound.body, inbound)
        if inv:
            c.say("dispute_invoice", number=inv.number)
        else:
            c.say("dispute_general")
        return Reply(c.text(), u.intent, "log_dispute", AWAITING_OWNER, meta={"dispute_id": d.id})

    def _send_invoice(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        invoices = tools.find_invoices(ctx, u.invoice_refs)
        if not invoices and u.invoice_refs:
            c.say("invoice_not_found")
            self._statement(ctx, c, with_payments=False)
            return Reply(c.text(), u.intent, "send_statement", ANSWERED)
        if not invoices:
            latest = tools.latest_invoice(ctx)
            invoices = [latest] if latest else []
        if not invoices:
            c.say("no_dues").synced(tools.synced_at(ctx))
            return Reply(c.text(), u.intent, "send_invoice", ANSWERED)
        for i in invoices[:3]:
            c.say("invoice_head", number=i.number, date=day(i.date))
            for line in i.lines:
                c.say("invoice_line", item=line.item_name, qty=qty_text(line.qty), unit=line.unit,
                      rate=money(line.rate_paise), amount=money(line.amount_paise))
            c.say("invoice_totals", sub=money(i.subtotal_paise), tax=money(i.tax_paise), total=money(i.total_paise))
            if i.balance_paise <= 0:
                c.say("invoice_paid")
            else:
                c.say("invoice_open", paid=money(i.paid_paise), balance=money(i.balance_paise), due=day(i.due_date))
        c.say("pdf_attached").synced(tools.synced_at(ctx))
        return Reply(c.text(), u.intent, "send_invoice", ANSWERED,
                     meta={"invoices": [i.number for i in invoices[:3]]},
                     attachments=[{"kind": "invoice", "invoice_id": i.id} for i in invoices[:3]])

    def _statement(self, ctx: Ctx, c: Composer, with_payments: bool) -> None:
        dues = tools.open_dues(ctx)
        if dues.invoices:
            c.say("statement_head")
            for i in dues.invoices:
                c.say("due_line", number=i.number, date=day(i.date), balance=money(i.balance_paise),
                      due=day(i.due_date))
            c.say("total", total=money(dues.total_paise))
        else:
            c.say("no_dues")
        if with_payments:
            paid = tools.last_receipts(ctx)
            if paid:
                c.say("last_payments")
                for r in paid:
                    c.say("receipt_line", amount=money(r.amount_paise), date=day(r.date), ref=receipt_ref(r))
        c.synced(tools.synced_at(ctx))

    def _send_statement(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        self._statement(ctx, c, with_payments=True)
        c.say("pdf_attached")
        return Reply(c.text(), u.intent, "send_statement", ANSWERED, attachments=[{"kind": "statement"}])

    def _balance_query(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        self._statement(ctx, c, with_payments=False)
        return Reply(c.text(), u.intent, "check_dues", ANSWERED)

    def _stock_lines(self, ctx: Ctx, c: Composer, items) -> None:
        c.say("stock_head")
        for it in items:
            key = "stock_line" if it.qty > 0 else "stock_out"
            c.say(key, item=it.name, qty=qty_text(it.qty), unit=it.unit, rate=money(it.rate_paise))
        c.synced(tools.synced_at(ctx), stock=True)

    def _stock_query(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        items = tools.find_stock(ctx, u.item_query or inbound.body)
        if not items:
            c.say("stock_not_found")
            return Reply(c.text(), u.intent, "handoff_owner", AWAITING_OWNER)
        self._stock_lines(ctx, c, items)
        return Reply(c.text(), u.intent, "stock_lookup", ANSWERED, meta={"items": [i.name for i in items]})

    def _order_request(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        items = tools.find_stock(ctx, u.item_query or inbound.body)
        c.say("order_noted")
        if items:
            self._stock_lines(ctx, c, items)
        return Reply(c.text(), u.intent, "handoff_owner", AWAITING_OWNER, meta={"items": [i.name for i in items]})

    def _opt_out(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        tools.pause_reminders(ctx)
        c.say("opt_out")
        return Reply(c.text(), u.intent, "pause_reminders", AWAITING_OWNER)

    def _smalltalk(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        if u.asks_identity:
            c.say("identity", company=ctx.company.name)
        else:
            c.say("smalltalk")
        return Reply(c.text(), u.intent, "smalltalk", ANSWERED)

    def _unclear(self, ctx: Ctx, u: Understanding, c: Composer, inbound: MessageLog) -> Reply:
        c.say("handoff")
        return Reply(c.text(), u.intent, "handoff_owner", AWAITING_OWNER)
