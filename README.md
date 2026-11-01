# Revert

An AI assistant for Indian manufacturers and distributors. It chases overdue payments
over WhatsApp and answers dealer questions (stock, rates, dues).

**Status: Phase 4 of 5.**
- Phase 1: data model, demo company, mock TallyPrime server, Tally XML connector, sync job, ageing.
- Phase 2: reminder engine, web chat simulator, owner dashboard.
- Phase 3: AI agent: "already paid", invoices and statements, promises to pay with follow-up,
  disputes, stock and rates, hand-off; English, Hindi and Kannada replies.
- Phase 4: WhatsApp Cloud API: replies, approved templates outside the 24-hour window, invoice and
  statement PDFs, payment screenshots, delivery receipts, signed webhook, dashboard password.

Next: the real Tally connector and CSV upload (Phase 5).

## Setup (Windows / macOS / Linux)

You need Python 3.11 or newer.

```bash
py -3.13 -m venv .venv            # macOS/Linux: python3 -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

Every dependency is free and needs no account. The dashboard loads HTMX from unpkg.com,
so the browser needs internet access the first time.

**The AI agent works without any account.** With no API key it reads messages with a built-in
offline classifier. For better understanding of free-form Hinglish and Kannada, add a free
Gemini key in `.env` (needs a Google account, free tier, no card):

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-from-aistudio.google.com/apikey
```

`LLM_PROVIDER=claude` (with `ANTHROPIC_API_KEY`) or `openai` (with `OPENAI_API_KEY`) work too,
but those are paid. If the LLM is down, rate-limited or returns nonsense, the agent falls back to
the offline classifier for that message. The dashboard footer shows which one is in use.

## Run

The quickest path needs one terminal:

```bash
python -m revert seed --offline   # reset the DB and load the demo company
python -m revert serve --offline  # dashboard + chat simulator
```

Then open:
- Dashboard: http://127.0.0.1:8000/
- Chat simulator: http://127.0.0.1:8000/simulator

`--offline` makes "Sync now" read the demo books in-process. To sync against the mock Tally
server over HTTP instead, the way real Tally will be used, run the mock in a second terminal:

```bash
# Terminal 1: the mock TallyPrime XML server on :9000
python -m revert.mock_tally

# Terminal 2
python -m revert seed             # resets the DB, syncs from (mock) Tally, applies demo tone/language prefs
python -m revert serve            # dashboard; syncs and runs reminders every 15 minutes in the background
```

Other commands:

```bash
python -m revert ageing [--buyer Mahadev]        # ageing report
python -m revert status                          # last sync time and row counts
python -m revert remind                          # run the reminder engine once, now
python -m revert remind --at "2026-11-02 10:30"  # ...as if it were that IST time (for testing quiet hours)
python -m revert pay "Mahadev Traders" 22770 --invoice INV-1040   # mock only: book a payment in "Tally"
python -m revert sync                            # pick it up; reminders for INV-1040 stop
python -m revert chat mahadev "50k now, rest on 15th"   # message the agent as a dealer
python -m revert wa-templates                    # WhatsApp template texts to submit to Meta
python -m revert whatsapp-check                  # test your WhatsApp token and phone number id
```

Tests: `pytest` (245 tests, about 15 seconds). They never call a real LLM or Meta.

## How it fits together

```
TallyPrime :9000 ──XML──► connectors/tally_xml ──Snapshot──► core/sync ──► DB ──► core/ageing
mock_tally :9000 ──same XML──┘                                     │          └──► core/reminders ──► core/outbox ──► channel
                                                                   └─ paid invoices stop their reminders             (WhatsApp, or the
WhatsApp ──signed webhook──► core/conversation ──► agent ──► reply (+ PDF) via core/outbox                     web simulator)
simulator ─────────────────┘           delivery receipts ──► message log (✓ ✓✓ read, failed → stage retried)

agent:  understand (LLM or offline rules) ──► tools (DB reads/writes) ──► reply templates (en/hi/kn) ──► guard ──► send
```

| Folder | What lives there |
|---|---|
| `revert/connectors/` | `base.py`: the connector interface and the records it returns. `tally_xml.py`: TDL Collection export requests and parsers (read-only, Export only). `mock_tally.py`: the same connector pointed at the mock |
| `revert/mock_tally/` | Stdlib HTTP server that answers Tally XML requests from the seed books. `/_mock/receipt` books a payment for demos |
| `revert/core/` | SQLAlchemy models, `sync.py`, `ageing.py`, `reminders.py` (the engine), `promises.py` (kept/broken + follow-ups), `outbox.py` (the only way messages go out), `conversation.py` (inbound), `jobs.py` (sync + reminders, shared by the CLI, buttons and scheduler) |
| `revert/channels/` | `base.py`: the channel interface and templates. `web_simulator.py`: delivery is the message log itself, so nothing leaves the laptop. `whatsapp.py`: Cloud API sending (text, templates, PDFs), media download, webhook signature check and parsing |
| `revert/agent/` | `understand.py` (intent + slots, LLM or offline rules), `llm.py` (Gemini/Claude/OpenAI over plain HTTPS), `parse.py` (amounts, dates, UTRs in English/Hindi/Kannada), `tools.py` (the only DB access), `guard.py` (checks every reply), `agent.py` (one handler per intent) |
| `revert/verticals/collections/` | Reminder templates (by tone and stage), agent reply texts in three languages (`replies.py`), and the inbound handler that calls the agent |
| `revert/dashboard/` | FastAPI app, Jinja2 templates, one CSS file. HTMX drives the tone/pause controls and the live chat thread |
| `revert/seed/` | Shree Ganesh Electricals demo data |

Conventions:
- **Money is integer paise.** No floats anywhere near amounts. Messages quote exact paise when there are any.
- **DB timestamps are UTC. Business dates are IST.**
- **Tally sends debits as negative numbers.** The connector flips them, so receivables
  are positive inside Revert.
- **Sync never overwrites Revert-owned fields:** tone, paused, language, and phones added manually.
- **Sync is all-or-nothing.** If Tally is down, a failed `SyncRun` is logged and both the data
  and `last_synced_at` are left as they were.
- **Every message goes through `core/outbox.py`.** It refuses any number not stored against the
  buyer and logs every message with intent, action and outcome.

## Reminder rules

| Rule | How it works |
|---|---|
| Schedule | Stages at -3, 0, +7, +15 and +30 days from the due date. Editable on the Reminder schedule page |
| One stage at a time | Each (invoice, stage) is sent at most once. If several stages were missed (first run, or a deferral), only the latest is sent, so dealers never get a backlog burst |
| One message per dealer | All of a dealer's invoices that are due a reminder go in a single message |
| Stop on payment | After every sync, any invoice that received money has its current stage cancelled. Fully paid invoices are never chased again; part-paid ones resume at their next stage |
| Quiet hours | Nothing is sent from 20:00 to 09:00 IST. Those reminders wait, they are not dropped |
| Weekly cap | At most 2 reminders per dealer in any rolling 7 days. Extra ones wait |
| Pause | A paused dealer gets no reminders until unpaused. A dealer who asks the agent to stop is paused too |
| Promise to pay | No reminders until the promised date. If it passes unpaid, one polite follow-up, then the normal schedule |
| Dispute | No reminders for the disputed invoice (or for the dealer, if no invoice was named) until the owner clicks Resolve |
| "Already paid" | Reminders wait 3 days while the owner checks. A claim is closed automatically when a synced receipt carries the same UTR |
| WhatsApp 24-hour rule | If the dealer hasn't written in 24 hours, the approved `revert_payment_reminder` template goes instead, in the dealer's language (Hindi or Kannada too) |
| Message content | Opens with "Shree Ganesh Electricals's assistant", quotes only invoice numbers and balances from the DB, ends with the last-synced time, and never threatens or mentions legal action in any tone |

Scheduled reminders are English only for now. Agent replies are in the dealer's language.

The **Recovered this month** figure counts payments this month against invoices Revert had
reminded about before the payment date. **Collected this month** is every receipt.

## The AI agent

| Dealer says | Agent does | Owner sees |
|---|---|---|
| "I already paid" / "paid 20k" | Looks for a matching receipt (recent, or same amount). Found: confirms with amount, date, reference and what's still pending. Not found: asks for the UTR or a screenshot, records a payment claim, holds reminders | Needs you, when not found |
| "UTR SBIN1234..." | Searches this dealer's receipts for the reference | Needs you, when not found |
| "send invoice 1043" / "send the bill" | Sends the invoice lines, GST, total, paid and pending | - |
| "send my statement" / "hisab bhejo" | Open invoices, total pending, last payments | - |
| "how much do I owe?" | Open invoices and total | - |
| "I'll pay Friday" / "50k now, rest on 15th" | Records each part with its date and amount, holds reminders, follows up once if a date passes unpaid | Promises due today |
| "Rate is wrong in INV-1043" / "short delivery" | Records a dispute, stops reminders for it | Needs you + Disputes |
| "Is 16A MCB available?" / "RCCB rate?" | Stock quantity and rate from the last sync | - |
| "Need 50 nos 32A MCB" | Thanks them, shows stock, passes the order on | Needs you |
| "Stop messaging me" | Pauses reminders | Needs you |
| "Are you a bot?" | Says it is the business's automated assistant, not a person | - |
| Anything else | Polite hand-off | Needs you |

An annoyed dealer gets an apology first, and the message also goes to Needs you.

**How the hard rules are enforced in code, not by prompt:**
- **No invented numbers.** The LLM only classifies the message. It never writes the reply. Replies
  come from fixed templates whose only numbers are placeholders filled from tool results. Before
  sending, `guard.py` checks every number in the reply against the values the tools read from the
  DB, plus what the dealer wrote. Anything else blocks the reply, undoes that turn's writes and
  hands the message to the owner. Amounts the LLM extracts (e.g. a promise of "50k") are kept
  only if the deterministic parser also finds them in the message. A test sends 17 kinds of
  message from all 25 demo dealers and checks every rupee figure against the DB.
- **Sync time.** Every reply with money or stock ends with the last-synced time.
- **Identity.** Every reply is signed "<Business>'s assistant" in the reply's language. When asked,
  the agent says it is automated.
- **No threats.** Legal words are blocked in English, Hindi and Kannada.
- **Only stored numbers.** Replies go through the outbox, which refuses unknown numbers.
  Messages from unknown numbers are logged and never answered.
- **Privacy between dealers.** Every tool is scoped to the sender: typing another dealer's invoice
  number or UTR finds nothing.
- **Everything logged.** Each inbound message records its intent, action, outcome, and what the
  agent understood (engine, language, extracted dates and amounts).

## WhatsApp setup

Everything works without WhatsApp: the simulator is the default. To use real WhatsApp you need a
Meta (Facebook) account, which only you can create. Steps:

1. **Create the app.** At developers.facebook.com, create an app of type *Business* and add the
   *WhatsApp* product. Meta gives you a free test business number.
2. **Copy the IDs.** On *WhatsApp > API Setup*, copy the temporary access token and the
   *Phone number ID*. Add your own mobile under "To" as a test recipient (the test number can
   message up to 5 verified numbers). The App secret is under *App settings > Basic*.
3. **Fill in `.env`:**
   ```bash
   CHANNEL=whatsapp
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_APP_SECRET=...
   WHATSAPP_VERIFY_TOKEN=any-random-string-you-choose
   DASHBOARD_PASSWORD=pick-a-password
   ```
   Then check them: `python -m revert whatsapp-check`.
4. **Expose the webhook.** Meta must reach `/webhooks/whatsapp` over HTTPS. On a laptop, use a
   tunnel, for example Cloudflare's quick tunnel (free, no account):
   `cloudflared tunnel --url http://localhost:8000`. **Set `DASHBOARD_PASSWORD` first**: the tunnel
   makes the whole app public, and the password keeps everything except the webhook locked.
5. **Register the webhook.** In *WhatsApp > Configuration*, set the Callback URL to
   `https://<your-tunnel>/webhooks/whatsapp` and the Verify token to your `WHATSAPP_VERIFY_TOKEN`,
   then subscribe to the **messages** field. Every call Meta makes is signed. Revert rejects
   unsigned ones and ignores Meta's duplicate retries.
6. **Submit the templates.** Run `python -m revert wa-templates` and create the two templates it
   prints in *WhatsApp Manager > Message templates* (category *Utility*, languages en, hi, kn).
   Until they're approved, reminders to dealers who haven't written in 24 hours fail. They show as
   failed and are retried on the next run.
7. **Try it.** Open a dealer's page (say Mahadev) and add your own mobile under *Phone numbers*.
   Message the test number from your phone ("how much do I owe?"); the agent answers on WhatsApp.
   Click *Run reminders*: you get the reminder template in Mahadev's language (Hindi).

How it behaves:
- **Demo dealers stay in the simulator.** Numbers that can't exist in India (`+91 0…`, all the seed
  data) are never sent to WhatsApp, so you can mix demo dealers with your own real number.
- **Free text vs templates.** Within 24 hours of a dealer's last message the agent and the owner
  can send anything. After that, only reminders and promise follow-ups go out, as templates. An
  owner reply outside the window is refused with an explanation.
- **PDFs.** "Send invoice 1043" or "send my statement" gets the text answer plus a PDF built from
  the books. The owner can open the same PDFs from the dealer's page.
- **Screenshots.** A photo or PDF from a dealer is saved under `MEDIA_DIR`, logged as "already
  paid", shown under Needs you with a link, and reminders wait 3 days. Voice notes and other media
  go to the owner.
- **Delivery receipts.** ✓ sent, ✓✓ delivered, blue ✓✓ read, ⚠ failed. A reminder that fails
  after sending (e.g. the number isn't on WhatsApp) frees its stage so the next run retries.
- **Tokens.** The temporary token expires after 24 hours. For daily use, create a System User token
  in Meta Business Settings.

**Cost.** The test number is free. On a real business number, Meta bills per template message
(reminders and follow-ups outside the 24-hour window; check Meta's current India rates). Replies
inside the 24-hour window are free.

## Demo data

Shree Ganesh Electricals, Peenya, Bangalore: 25 dealers (English/Hindi/Kannada),
100 invoices, 15 stock items. All dates are relative to today. Most invoices fall in the
last 90 days; a handful go back to about 130 days so the 90+ bucket has entries.
Phone numbers are deliberately invalid (`+91 00000 xxxxx`) so nothing can reach a real person.

Planted stories:
1. **Sharma Electricals.** Loyal dealer who always pays on time. One invoice is 4 days late. Tone is gentle.
2. **Venkatesh Power Solutions.** INV-1043 billed the RCCB at ₹1,180; their PO said ₹1,150. Every other invoice is paid.
3. **Mahadev Traders.** Habitually late payer. Exactly ₹80,000 pending across three invoices, overdue up to 50 days.

## 10-minute demo script

Setup before the demo: start the mock (`python -m revert.mock_tally`), run `python -m revert seed`,
then `python -m revert serve --no-scheduler` so nothing fires on its own while you talk.
Open http://127.0.0.1:8000/simulator. The dealer's phone is on the left and the owner's dashboard on the right.

1. **(1 min) The owner's view.** The dashboard lists everyone overdue, most days late first, with the
   last-synced time from Tally. Point out Mahadev at ₹80,000 and the 90+ dealers at the top.
2. **(30 s) Tone and pause.** Set Mahadev's tone to firm. Pause one dealer. Both save instantly.
3. **(30 s) Quiet hours.** Open "as if at…", pick 10 PM today and click Run reminders. Everything
   is deferred: no messages at night.
4. **(1 min) Reminders.** Clear the time and click Run reminders. Pick Mahadev on the left:
   one message lists all three invoices, totals ₹80,000, signs as the business's assistant
   and quotes the sync time. The paused dealer got nothing. Click again: nothing is sent twice.
5. **(1 min) "Already paid".** As Mahadev, type "I already paid 20k last week". No such receipt
   is in the books, so the assistant asks for the UTR or a screenshot. It shows under **Needs you**.
6. **(1 min) Promise.** Still as Mahadev, type "50k now, rest on 15th". Both parts are recorded:
   ₹50,000 today and the remaining ₹30,000 on the 15th, from his ₹80,000 balance. Mahadev shows
   under **Promises due today**.
7. **(1 min) Dispute.** Switch to Venkatesh Power Solutions: "INV-1043 rate is wrong, our PO said 1150".
   The dispute appears under **Disputes**, and reminders for INV-1043 stop until you click Resolve.
8. **(1 min) Stock and languages.** As Sharma: "Is 16A MCB available?" (quantity and rate from Tally).
   As Patel Electric House: "मेरा कितना बाकी है?" (the answer comes back in Hindi). Click Run reminders:
   the banner shows who is held back by a promise, a payment claim or a dispute.
9. **(1 min) Payment stops reminders.** In a terminal:
   `python -m revert pay "Mahadev Traders" 22770 --invoice INV-1040`, then click **Sync now**.
   The banner says INV-1040 was paid and its reminders stopped. "Recovered this month" goes up by ₹22,770.
10. **(1 min) Tally down.** Stop the mock and click Sync now. It fails cleanly and the data stays.

## Known limits

- The Tally XML field names (`BILLCREDITPERIOD`, `STANDARDPRICELIST`, `LEDGERMOBILE`, …) follow
  TallyPrime's Collection export. They will be checked against a real TallyPrime in Phase 5.
- Invoices deleted in Tally are not removed from Revert yet.
- Screenshots are stored and shown to the owner, not read automatically. The owner matches them to Tally.
- One business number per installation. Template names are fixed in `.env`; tone applies only to
  free-text reminders (templates are tone-neutral).
- The Hindi and Kannada texts are simple business language. Have a native speaker review them before going live.
- The offline classifier knows common phrasings. Unusual wording falls to "unclear" and goes to the owner.
  A Gemini key covers much more.
- Orders are not placed in Tally (Revert is read-only). They go to the owner to book.
- The dashboard has one shared password (`DASHBOARD_PASSWORD`), no user accounts. Without it,
  keep the app on your own machine.
