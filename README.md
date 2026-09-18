# AI Email Triage Agent

An autonomous agent that reads inbound customer email, classifies and extracts
structured data with an LLM, and **creates a ticket in Notion** — no human in the loop.

Built as a reference implementation for small industrial manufacturers who still
sort their inbox by hand.

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│ Mailbox      │────▶│  Agent        │────▶│ Notion       │
│ (IMAP)       │     │  (Python)     │     │ database     │
└──────────────┘     └───────┬───────┘     └──────────────┘
                             │  structured output (tool-use)
                             ▼
                     ┌───────────────┐
                     │  Claude API   │
                     └───────────────┘
```

## What it does

1. **Ingest** — polls a mailbox through one of two interchangeable backends selected by
   `INGESTION` in `.env`: **Gmail API** (OAuth) or **IMAP** (`BODY.PEEK`, so nothing is
   marked read before it is safely stored). Both yield the same `IncomingEmail`, decoding
   MIME headers and multipart bodies and falling back from `text/plain` to stripped
   `text/html`.
2. **Classify & extract** — one LLM call per email with **forced tool-use**, so the model
   can only answer with a JSON object matching a fixed schema: category, sender, English
   summary, priority, resolved deadline, language, action-required flag, confidence.
   Three few-shot examples are replayed as a tool-use conversation.
3. **Act** — creates a Notion page with typed properties plus the original email body as
   page content.
4. **Never lose an email** — a failure leaves the message unread in the mailbox *and*
   appends it to a replayable `data/failed_queue.jsonl`. Duplicate protection is by
   `Message-ID`. Retries use exponential backoff with jitter.

## Categories

| Category | Meaning |
|---|---|
| `technical_support` | existing client reports a bug, outage, or asks how to use the product |
| `sales_inquiry` | prospect or client asks about a new project, quote, pricing, capability |
| `complaint` | dissatisfaction with delivery, quality, billing or service already provided |
| `administrative` | invoices, contracts, legal / tax / accounting paperwork |
| `other` | newsletters, cold outreach, notifications — `action_required: false` |

Priority is `low | medium | high | urgent`; `urgent` is reserved for a blocked production
line or an explicit sub-48h ultimatum.

## Quick start

```bash
git clone <repo> && cd ai-agent-email-triage
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill it in
```

**1. Test mailbox** — never a personal or school address; create a dedicated account.

*Gmail API (`INGESTION=gmail_api`, default):* in Google Cloud Console create a project,
enable the **Gmail API**, configure the OAuth consent screen as *External / Testing* and add
the demo address as a test user, then create an **OAuth client ID → Desktop app** and save
the downloaded JSON as `credentials.json` in the repo root. The first run opens a browser
once and caches a refresh token in `token.json`. No App Password, no 2FA requirement.

*IMAP (`INGESTION=imap`):* enable 2FA on the account, generate an App Password and put it in
`IMAP_PASSWORD`. Kept for clients who are not on Google.

Verify credentials before seeding:

```bash
python -m scripts.seed_mailbox --check-login
```

**2. Seed it with the 18 labelled fixtures** — appended over IMAP, so no mail is
actually sent:

```bash
python -m scripts.seed_mailbox --dry-run   # preview
python -m scripts.seed_mailbox             # append as unread
python -m scripts.seed_mailbox --purge     # clean up afterwards
```

**3. Notion** — create an internal integration at <https://notion.so/my-integrations>,
share a page with it, then:

```bash
python -m scripts.setup_notion --parent-page <page_id>
# prints the NOTION_DATABASE_ID to paste into .env
```

**4. Run**

```bash
python -m app.main --fixtures       # local fixtures, no mailbox needed
python -m app.main --once           # one pass over unread mail
python -m app.main --loop           # continuous polling
python -m app.main --replay-failed  # drain the failed queue
```

Set `DRY_RUN=true` to run the whole pipeline and log what *would* be written to Notion.
Set `LLM_PROVIDER=mock` to run with no API key at all (keyword heuristic, same interface).

## Accuracy

`scripts/evaluate.py` scores the classifier against the 18 hand-labelled fixtures
(French and English, 5 categories) and prints a confusion matrix:

```bash
python -m scripts.evaluate          # the configured LLM
python -m scripts.evaluate --mock   # offline heuristic baseline
```

The `mock` heuristic baseline scores **89% category / 50% priority** — it exists to make
the LLM's uplift measurable, not to be used in production.

## Layout

```
app/
  config.py         env-backed config + validation
  ingest.py         backend factory (Reader protocol)
  email_reader.py   IMAP ingestion, MIME parsing, shared IncomingEmail
  gmail_reader.py   Gmail API ingestion over OAuth
  classifier.py     tool-use schema, prompt, few-shot, Anthropic + mock providers
  notion_writer.py  Notion page creation, duplicate guard, failed queue
  retry.py          exponential backoff (no third-party dependency)
  main.py           orchestration, CLI, processed log
scripts/
  seed_mailbox.py   load fixtures into the demo mailbox over IMAP APPEND
  setup_notion.py   create the destination database with the right schema
  evaluate.py       accuracy report against labelled fixtures
fixtures/
  sample_emails.json  18 realistic labelled emails
```

## Design notes

- **Forced tool-use over JSON-mode prompting.** The schema is the contract; the model
  cannot return prose, and enum violations are rejected client-side in `_normalise`.
- **Acknowledge last.** A message is flagged `\Seen` only after Notion confirms the write,
  so a crash mid-pipeline replays instead of losing work.
- **Both ends are swappable.** Ingestion (`Reader`) and classification (`Provider`) are
  `Protocol`s: Gmail API ↔ IMAP is one line in `.env`, and swapping Claude for another model,
  or Notion for Airtable/a CRM, touches one module.
- **Idempotency by `Message-ID`,** stored on the Notion page, so re-running is safe.

## Security

- Test mailbox only, populated with synthetic emails — no third-party personal data.
- All credentials in `.env`, which is git-ignored; `.env.example` documents the shape.
- OAuth with the narrowest scopes that work (`gmail.modify`, `gmail.insert`) — no send
  permission; `credentials.json` and `token.json` are git-ignored. On the IMAP path, an App
  Password rather than the account password, revoked after the demo.
- The demo is shown as a recorded GIF rather than live access to a running system.

## Adapting it

The destination is one module. The same pipeline writes to Airtable, HubSpot, Odoo or an
internal ticketing table by replacing `notion_writer.py` — the ingestion, schema and
guarantees stay identical.
