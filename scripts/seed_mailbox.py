#!/usr/bin/env python3
"""Load fixtures/sample_emails.json into the demo mailbox as unread messages.

Uses IMAP APPEND (no outbound mail is sent, nothing lands in Sent).

    python -m scripts.seed_mailbox            # append all 18
    python -m scripts.seed_mailbox --dry-run  # just print what would be added
    python -m scripts.seed_mailbox --purge    # delete previously seeded messages
"""
from __future__ import annotations

import argparse
import imaplib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import config  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sample_emails.json"
SEED_HEADER = "X-Demo-Fixture"


def build_message(item: dict, sent_at: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f'{item["from_name"]} <{item["from_email"]}>'
    msg["To"] = config.imap_user
    msg["Subject"] = item["subject"]
    msg["Date"] = format_datetime(sent_at)
    msg["Message-ID"] = make_msgid(domain="demo.fixture")
    msg[SEED_HEADER] = item["id"]
    msg.set_content(item["body"])
    return msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge", action="store_true", help="delete seeded fixtures instead")
    args = ap.parse_args()

    items = json.loads(FIXTURES.read_text(encoding="utf-8"))
    print(f"{len(items)} fixture(s) loaded from {FIXTURES.name}")

    if args.dry_run:
        for it in items:
            print(f"  {it['id']}  {it['expected']['category']:<18} {it['subject'][:60]}")
        return 0

    config.require_imap()
    conn = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    conn.login(config.imap_user, config.imap_password)
    conn.select(config.imap_folder)

    if args.purge:
        status, data = conn.uid("SEARCH", None, "HEADER", SEED_HEADER, "F")
        uids = data[0].split() if status == "OK" else []
        for uid in uids:
            conn.uid("STORE", uid.decode(), "+FLAGS", "(\\Deleted)")
        conn.expunge()
        print(f"Purged {len(uids)} seeded message(s)")
        conn.logout()
        return 0

    base = datetime.now(timezone.utc) - timedelta(hours=len(items))
    for i, item in enumerate(items):
        sent_at = base + timedelta(hours=i, minutes=(i * 7) % 60)
        msg = build_message(item, sent_at)
        status, _ = conn.append(
            config.imap_folder,
            "",  # no \Seen flag -> arrives unread
            imaplib.Time2Internaldate(time.mktime(sent_at.timetuple())),
            msg.as_bytes(),
        )
        print(f"  {item['id']} -> {status}")
    conn.logout()
    print("Done. Run: python -m app.main --once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
