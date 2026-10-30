# Revert

An AI assistant for Indian manufacturers and distributors. It chases overdue payments
over WhatsApp and answers dealer questions (stock, rates, dues).

**Status: Phase 1 of 5.** Done: data model, demo company, mock TallyPrime server,
Tally XML connector, sync job, ageing.

## Setup (Windows / macOS / Linux)

You need Python 3.11 or newer.

```bash
py -3.13 -m venv .venv            # macOS/Linux: python3 -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

## Run

```bash
# Terminal 1: the mock TallyPrime XML server on :9000
python -m revert.mock_tally

# Terminal 2
python -m revert seed             # resets the DB, syncs from (mock) Tally, applies demo tone/language prefs
python -m revert ageing           # ageing report, all buyers
python -m revert ageing --buyer Mahadev
python -m revert status           # last sync time and row counts
python -m revert pay "Mahadev Traders" 22770 --invoice INV-1040   # mock only: book a payment in "Tally"
python -m revert sync             # picks the payment up and reports "Payments received against: INV-1040"
```

No second terminal? Run `python -m revert seed --offline`. It serves the demo books
in-process, and the data still goes through the full XML request/parse path.

Tests: `pytest`

## How it fits together

```
TallyPrime :9000  ──XML──►  connectors/tally_xml  ──Snapshot──►  core/sync  ──►  DB  ──►  core/ageing
mock_tally :9000  ──same XML──┘ (connectors/mock_tally = same code, different URL)
```

| Folder | What lives there |
|---|---|
| `revert/connectors/` | `base.py`: the connector interface and the records it returns. `tally_xml.py`: TDL Collection export requests and parsers (read-only, Export only). `mock_tally.py`: the same connector pointed at the mock |
| `revert/mock_tally/` | Stdlib HTTP server that answers Tally XML requests from the seed books. `/_mock/receipt` books a payment for demos |
| `revert/core/` | SQLAlchemy models, `sync.py`, `ageing.py`, money/phone/time helpers |
| `revert/seed/` | Shree Ganesh Electricals demo data |
| `revert/agent`, `verticals/collections`, `channels`, `dashboard` | Empty placeholders, filled in Phases 2–4 |

Conventions:
- **Money is integer paise.** No floats anywhere near amounts.
- **DB timestamps are UTC. Business dates are IST.**
- **Tally sends debits as negative numbers.** The connector flips them, so receivables
  are positive inside Revert.
- **Sync never overwrites Revert-owned fields:** tone, paused, language, and phones added manually.
- **Sync is all-or-nothing.** If Tally is down, a failed `SyncRun` is logged and both the data
  and `last_synced_at` are left as they were. Every amount the agent quotes will carry
  `last_synced_at`.
- **`message_log` exists from day one** and records intent, action and outcome for every message.

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

*Phase 1 covers the data side. Chat and dashboard steps are added in Phases 2–3.*

1. (1 min) Start `python -m revert.mock_tally`. Open http://localhost:9000 and it replies
   "TallyPrime Server is Running", just like real Tally.
2. (2 min) Run `python -m revert seed`, then `python -m revert ageing`. Walk through the buckets.
   Point out the 90+ column and the dealers at the top of the list.
3. (2 min) Run `python -m revert ageing --buyer Mahadev` to show his three invoices, total ₹80,000.
4. (2 min) Record a payment: `python -m revert pay "Mahadev Traders" 22770 --invoice INV-1040`,
   then `python -m revert sync`. Revert reports the payment against INV-1040, and from Phase 2
   that is what stops the reminders.
5. (1 min) Stop the mock and run `python -m revert sync`. It fails cleanly and the existing data stays.

## Known limits

- The Tally XML field names (`BILLCREDITPERIOD`, `STANDARDPRICELIST`, `LEDGERMOBILE`, …) follow
  TallyPrime's Collection export. They will be checked against a real TallyPrime in Phase 5.
- Invoices deleted in Tally are not removed from Revert yet.
