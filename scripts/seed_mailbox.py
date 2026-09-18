#!/usr/bin/env python3
"""Load fixtures/sample_emails.json into the demo mailbox as unread messages.

Nothing is sent: IMAP APPEND or Gmail messages.insert put the message straight
into the mailbox. The backend follows INGESTION in .env.

    python -m scripts.seed_mailbox --dry-run      # print what would be added
    python -m scripts.seed_mailbox --check-login  # verify credentials only
    python -m scripts.seed_mailbox                # seed all 18
    python -m scripts.seed_mailbox --purge        # remove seeded messages
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ConfigError, config  # noqa: E402
from app.ingest import get_reader  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sample_emails.json"
SEED_HEADER = "X-Demo-Fixture"
SEED_MARK = "nc-dev-demo-fixture"


def build_message(item: dict, sent_at: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f'{item["from_name"]} <{item["from_email"]}>'
    msg["To"] = config.imap_user or "me"
    msg["Subject"] = item["subject"]
    msg["Date"] = format_datetime(sent_at)
    msg["Message-ID"] = make_msgid(domain="demo.fixture")
    msg[SEED_HEADER] = f'{SEED_MARK}-{item["id"]}'
    msg.set_content(item["body"])
    return msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-login", action="store_true", help="verify credentials and exit")
    ap.add_argument("--purge", action="store_true", help="delete seeded fixtures instead")
    ap.add_argument("--reset-unread", action="store_true",
                    help="mark seeded fixtures unread again (re-record the demo)")
    args = ap.parse_args()

    items = json.loads(FIXTURES.read_text(encoding="utf-8"))
    print(f"{len(items)} fixture(s) loaded from {FIXTURES.name}")

    if args.dry_run:
        for it in items:
            print(f"  {it['id']}  {it['expected']['category']:<18} {it['subject'][:60]}")
        return 0

    try:
        reader = get_reader()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Could not connect via {config.ingestion}: {exc}", file=sys.stderr)
        return 3

    if args.check_login:
        print(f"Login OK via {reader.name}")
        return 0

    if args.purge:
        removed = reader.purge(SEED_HEADER, SEED_MARK)
        print(f"Purged {removed} seeded message(s)")
        return 0

    if args.reset_unread:
        n = reader.reset_unread(SEED_HEADER, SEED_MARK)
        print(f"{n} fixture(s) back to unread")
        return 0

    base = datetime.now(timezone.utc) - timedelta(hours=len(items))
    for i, item in enumerate(items):
        sent_at = base + timedelta(hours=i, minutes=(i * 7) % 60)
        status = reader.append(build_message(item, sent_at).as_bytes(), sent_at)
        print(f"  {item['id']} -> {status}")
    print("Done. Run: python -m app.main --once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
