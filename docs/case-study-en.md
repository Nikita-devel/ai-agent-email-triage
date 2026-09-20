# AI Email Triage Agent

**An autonomous agent that reads inbound customer email, classifies it, and creates a
structured ticket in Notion — with no human in the loop.**

---

## The problem

Small manufacturers receive everything through one mailbox: a stopped production line, a
request for a quote, an invoice dispute, a URSSAF deadline, and a dozen newsletters. All of
it arrives in the same inbox, and someone — usually the person with the least time to spare
— sorts it by hand and re-types it into whatever system the company uses.

Two things go wrong. Urgent requests sit unread behind newsletters. And requests that were
answered verbally leave no trace, so nobody can say afterwards how many complaints came in
last quarter, or how long a quote took to answer.

## The solution

An agent that polls the mailbox, understands each message, and writes a ticket into the
company's own tool. Each ticket carries a category, a priority, a one-line summary in
English, the deadline mentioned in the message, the detected language, and the original
email body.

Five categories: technical support, sales inquiry, complaint, administrative, other.
Four priority levels, from low to urgent, assigned by consequence and time — a stopped
production line or a 48-hour ultimatum is urgent; a legal deadline inside two weeks is high;
a sender who explicitly says "no rush" is low, even when reporting a fault.

## How it works

```
Mailbox (Gmail API / IMAP) → AI agent (Python) → Notion database
                                   ↓
                             Claude API
```

The agent reads unread messages, sends each one to Claude, and writes the result. One pass
over eighteen messages takes about two minutes and costs a few cents.

The classification step uses **forced tool-use**: the model is given a JSON schema and can
only answer by filling it in. It cannot return prose, and values outside the allowed enums
are rejected client-side before they ever reach Notion. That is the difference between a
demo and something a company can rely on.

## Results

Measured against eighteen hand-labelled emails in French and English, covering all five
categories:

| Metric | Result |
|---|---|
| Category accuracy | 18/18 (100%) |
| Deadline detection | 18/18 (100%) |
| Priority, exact match | 14/18 (78%) |
| Priority, within one level | 17/18 (94%) |

Priority is an ordinal judgement, not a fact: three of the four disagreements were a single
level apart, on messages where two people would reasonably disagree too. The accuracy report
ships with the project and can be re-run on any new set of examples — including a client's
own, which is how the system should be validated before it goes live.

## Reliability

Mid-project, Notion released a new API version that moved database properties into a new
data model and removed an endpoint the integration used. The integration broke.

Not one email was lost. Failed writes went to a replayable queue, the messages stayed
unread in the mailbox, and once the API version and SDK were pinned, the whole batch was
processed from scratch with no manual re-entry.

That behaviour is deliberate:

- A message is marked read **only after** the ticket is confirmed written. A crash halfway
  through replays instead of losing work.
- Every failure is written to a queue that can be drained with one command.
- Duplicates are impossible: each ticket carries the email's `Message-ID`, and the agent
  checks before creating.
- The API version and the SDK are both pinned, so a provider redesign cannot silently break
  a running deployment.

## Stack

Python · Claude API (forced tool-use) · Gmail API (OAuth) with IMAP as an alternative ·
Notion API · four runtime dependencies in total.

The mailbox and the destination are separate modules behind a shared interface. Switching
from Gmail to IMAP is one line in the configuration file; replacing Notion with Airtable,
HubSpot, Odoo or an internal ticket table means rewriting one file.

## Security

The demo runs on a dedicated mailbox filled with synthetic emails — no third-party personal
data. OAuth scopes are the narrowest that work: the agent can read mail and mark it read,
but has **no permission to send**. Credentials live in environment variables, never in the
repository. The agent reads only a dedicated label, not the whole mailbox.

## Adapting this to your company

The pipeline is the same whatever the destination. What changes for each client is the list
of categories, the priority rules, and the tool the tickets land in — all three are
configuration, not rewrites.

The honest way to start is a measurement: fifty of your own emails, labelled by someone who
knows the business, run through the classifier. That produces a real accuracy figure for
your mail, not for mine, and it costs an afternoon. If the number is good, the integration
is a few days' work. If it is not, you have lost an afternoon instead of a project.
