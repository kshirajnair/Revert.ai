# Revert

An AI assistant for Indian manufacturers and distributors. It chases overdue payments
over WhatsApp and answers dealer questions (stock, rates, dues).

**Status: Phase 2 of 5.**
- Phase 1: data model, demo company, mock TallyPrime server, Tally XML connector, sync job, ageing.
- Phase 2: reminder engine, web chat simulator, owner dashboard.

Next: the AI agent (Phase 3), WhatsApp (Phase 4), the real Tally connector and CSV upload (Phase 5).

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
```

Tests: `pytest` (112 tests, about 10 seconds).

## How it fits together

```
TallyPrime :9000 ──XML──► connectors/tally_xml ──Snapshot──► core/sync ──► DB ──► core/ageing
mock_tally :9000 ──same XML──┘                                     │          └──► core/reminders ──► core/outbox ──► channel
                                                                   └─ paid invoices stop their reminders             (web simulator now,
dealer message ──► channel ──► core/conversation ──► verticals/collections/handler ──► reply via core/outbox   WhatsApp in Phase 4)
```

| Folder | What lives there |
|---|---|
| `revert/connectors/` | `base.py`: the connector interface and the records it returns. `tally_xml.py`: TDL Collection export requests and parsers (read-only, Export only). `mock_tally.py`: the same connector pointed at the mock |
| `revert/mock_tally/` | Stdlib HTTP server that answers Tally XML requests from the seed books. `/_mock/receipt` books a payment for demos |
| `revert/core/` | SQLAlchemy models, `sync.py`, `ageing.py`, `reminders.py` (the engine), `outbox.py` (the only way messages go out), `conversation.py` (inbound), `jobs.py` (sync + reminders, shared by the CLI, buttons and scheduler) |
| `revert/channels/` | `base.py`: the channel interface. `web_simulator.py`: delivery is the message log itself, so nothing leaves the laptop |
| `revert/verticals/collections/` | Reminder templates (by tone and stage) and the inbound handler. Phase 2 hands every dealer message to the owner; Phase 3 swaps in the AI agent |
| `revert/dashboard/` | FastAPI app, Jinja2 templates, one CSS file. HTMX drives the tone/pause controls and the live chat thread |
| `revert/seed/` | Shree Ganesh Electricals demo data |
| `revert/agent/` | Empty placeholder, filled in Phase 3 |

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
| Pause | A paused dealer gets no reminders until unpaused |
| Message content | Opens with "Shree Ganesh Electricals's assistant", quotes only invoice numbers and balances from the DB, ends with the last-synced time, and never threatens or mentions legal action in any tone |

Messages are English only for now. Hindi and Kannada come with the AI agent in Phase 3.

The **Recovered this month** figure counts payments this month against invoices Revert had
reminded about before the payment date. **Collected this month** is every receipt.

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
2. **(1 min) Tone and pause.** Set Mahadev's tone to firm. Pause one dealer. Both save instantly.
3. **(1 min) Quiet hours.** Open "as if at…", pick 10 PM today and click Run reminders. Everything
   is deferred: no messages at night.
4. **(2 min) Reminders.** Clear the time and click Run reminders. Pick Mahadev on the left:
   one message lists all three invoices, totals ₹80,000, signs as the business's assistant
   and quotes the sync time. The paused dealer got nothing. Click again: nothing is sent twice.
5. **(2 min) Dealer replies.** As Mahadev, type "I already paid 20k last week". The assistant
   acknowledges and hands it to the owner, and it appears under **Needs you** on the right.
   Open Mahadev's page from there and reply as the owner.
6. **(2 min) Payment stops reminders.** In a terminal:
   `python -m revert pay "Mahadev Traders" 22770 --invoice INV-1040`, then click **Sync now**.
   The banner says INV-1040 was paid and its reminders stopped. "Recovered this month" goes up by ₹22,770.
7. **(1 min) Tally down.** Stop the mock and click Sync now. It fails cleanly and the data stays.

Phase 3 adds the AI agent: understanding "I already paid", promises to pay, disputes such as
Venkatesh's INV-1043, and stock questions.

## Known limits

- The Tally XML field names (`BILLCREDITPERIOD`, `STANDARDPRICELIST`, `LEDGERMOBILE`, …) follow
  TallyPrime's Collection export. They will be checked against a real TallyPrime in Phase 5.
- Invoices deleted in Tally are not removed from Revert yet.
- Dealer messages all go to the owner until the agent arrives in Phase 3.
- The dashboard has no login. Run it on your own machine only until auth is added.
