"""Pipeline entry point: IMAP -> LLM triage -> Notion.

    python -m app.main --once             # one pass over unread mail
    python -m app.main --loop             # poll every POLL_INTERVAL_SECONDS
    python -m app.main --fixtures         # run on fixtures/, no mailbox needed
    python -m app.main --replay-failed    # retry everything in data/failed_queue.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .classifier import TriageResult, get_provider
from .config import ConfigError, config
from .email_reader import IncomingEmail, fetch_unseen, mark_seen
from .notion_writer import NotionWriter, record_failure

log = logging.getLogger("agent")
_stop = False


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _handle_sigterm(*_args) -> None:
    global _stop
    _stop = True
    log.info("Stop requested, finishing current batch...")


def load_fixture_emails() -> list[IncomingEmail]:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "sample_emails.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    return [
        IncomingEmail(
            uid=f"fixture-{it['id']}",
            message_id=f"<{it['id']}@demo.fixture>",
            sender_name=it["from_name"],
            sender_email=it["from_email"],
            subject=it["subject"],
            body=it["body"],
            received_at=now,
        )
        for it in items
    ]


def log_processed(item: IncomingEmail, result: TriageResult, page_id: str) -> None:
    entry = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "uid": item.uid,
        "message_id": item.message_id,
        "subject": item.subject,
        "triage": result.to_dict(),
        "notion_page_id": page_id,
    }
    with config.processed_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def process_batch(emails: list[IncomingEmail], provider, writer: NotionWriter,
                  ack: bool = True) -> dict[str, int]:
    stats = {"ok": 0, "skipped": 0, "failed": 0}
    for item in emails:
        result: TriageResult | None = None
        try:
            if writer.exists(item.message_id):
                log.info("Already in Notion, skipping: %s", item.subject[:60])
                stats["skipped"] += 1
                if ack:
                    mark_seen(item.uid)
                continue

            result = provider.classify(item)
            log.info("%-18s %-7s conf=%.2f | %s",
                     result.category, result.priority, result.confidence, item.subject[:55])

            page = writer.create_ticket(item, result)
            log_processed(item, result, page.get("id", ""))
            if ack:
                mark_seen(item.uid)  # only after a confirmed write
            stats["ok"] += 1
        except Exception as exc:
            log.exception("Failed on uid=%s", item.uid)
            record_failure(item, result, exc)
            stats["failed"] += 1  # left UNSEEN in the mailbox -> retried next run
    return stats


def replay_failed(provider, writer: NotionWriter) -> dict[str, int]:
    path = config.failed_queue_path
    if not path.exists():
        log.info("No failed queue at %s", path)
        return {"ok": 0, "skipped": 0, "failed": 0}
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    emails = [IncomingEmail(**e["email"]) for e in entries]
    log.info("Replaying %d failed item(s)", len(emails))
    stats = process_batch(emails, provider, writer, ack=False)
    if stats["failed"] == 0:
        path.rename(path.with_suffix(".jsonl.done"))
        log.info("Failed queue drained")
    return stats


def run_once(provider, writer: NotionWriter, source: str) -> dict[str, int]:
    if source == "fixtures":
        emails = load_fixture_emails()
        log.info("Loaded %d fixture email(s)", len(emails))
        return process_batch(emails, provider, writer, ack=False)
    emails = fetch_unseen()
    if not emails:
        log.info("No new mail")
        return {"ok": 0, "skipped": 0, "failed": 0}
    return process_batch(emails, provider, writer, ack=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI email triage agent")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="single pass over unread mail (default)")
    mode.add_argument("--loop", action="store_true", help="poll continuously")
    mode.add_argument("--fixtures", action="store_true", help="run on local fixtures, no IMAP")
    mode.add_argument("--replay-failed", action="store_true", help="retry data/failed_queue.jsonl")
    args = ap.parse_args()

    _setup_logging()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        provider = get_provider()
        writer = NotionWriter()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    if config.dry_run:
        log.warning("DRY_RUN=true - nothing will be written to Notion")

    if args.replay_failed:
        stats = replay_failed(provider, writer)
    elif args.loop:
        stats = {"ok": 0, "skipped": 0, "failed": 0}
        while not _stop:
            batch = run_once(provider, writer, "imap")
            for k in stats:
                stats[k] += batch[k]
            for _ in range(config.poll_interval):
                if _stop:
                    break
                time.sleep(1)
    else:
        stats = run_once(provider, writer, "fixtures" if args.fixtures else "imap")

    log.info("Done | created=%(ok)d skipped=%(skipped)d failed=%(failed)d", stats)
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
