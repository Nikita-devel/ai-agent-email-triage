"""Write triage results into a Notion database (the agent's real-world action)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .retry import retry

from .classifier import TriageResult
from .config import Config, config
from .email_reader import IncomingEmail

log = logging.getLogger(__name__)

# Pinned deliberately, both the wire version and the SDK (see requirements.txt):
# Notion's 2025-09-03 API moved properties into "data sources" and removed
# databases.query, and notion-client >= 2.3 reshapes request bodies for that
# model whatever version is requested. Pinning both keeps a provider-side change
# from silently breaking a client deployment.
NOTION_API_VERSION = "2022-06-28"

# Property names expected in the Notion database (see scripts/setup_notion.py)
PROPS = {
    "title": "Request",
    "category": "Category",
    "priority": "Priority",
    "sender": "Sender",
    "email": "Email",
    "summary": "Summary",
    "deadline": "Deadline",
    "language": "Language",
    "action": "Action required",
    "confidence": "Confidence",
    "received": "Received",
    "message_id": "Message-ID",
    "status": "Status",
}


class NotionWriteError(RuntimeError):
    pass


def build_properties(item: IncomingEmail, result: TriageResult) -> dict:
    props: dict = {
        PROPS["title"]: {"title": [{"text": {"content": item.subject[:200]}}]},
        PROPS["category"]: {"select": {"name": result.category}},
        PROPS["priority"]: {"select": {"name": result.priority}},
        PROPS["sender"]: {"rich_text": [{"text": {"content": result.sender_name[:200]}}]},
        PROPS["email"]: {"email": item.sender_email or None},
        PROPS["summary"]: {"rich_text": [{"text": {"content": result.summary[:1900]}}]},
        PROPS["language"]: {"select": {"name": result.language or "unknown"}},
        PROPS["action"]: {"checkbox": result.action_required},
        PROPS["confidence"]: {"number": round(result.confidence, 2)},
        PROPS["message_id"]: {"rich_text": [{"text": {"content": item.message_id[:200]}}]},
        PROPS["status"]: {"select": {"name": "New"}},
    }
    if result.extracted_deadline:
        props[PROPS["deadline"]] = {"date": {"start": result.extracted_deadline}}
    if item.received_at:
        props[PROPS["received"]] = {"date": {"start": item.received_at}}
    return props


def build_children(item: IncomingEmail) -> list[dict]:
    """Keep the original email body on the page, chunked to Notion's 2000-char limit."""
    body = item.body or "(empty body)"
    chunks = [body[i:i + 1900] for i in range(0, min(len(body), 7600), 1900)]
    blocks = [{
        "object": "block", "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "Original email"}}]},
    }]
    blocks += [{
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
    } for chunk in chunks]
    return blocks


class NotionWriter:
    def __init__(self, cfg: Config = config) -> None:
        cfg.require_notion()
        self.cfg = cfg
        self.dry_run = cfg.dry_run
        self.client = None
        if not self.dry_run:
            from notion_client import Client
            from notion_client.errors import APIResponseError, HTTPResponseError

            self._retryable = (APIResponseError, HTTPResponseError)
            self.client = Client(auth=cfg.notion_api_key,
                                 notion_version=NOTION_API_VERSION)
        else:
            self._retryable = (Exception,)

    def exists(self, message_id: str) -> bool:
        """Idempotency guard: don't create a duplicate ticket for the same Message-ID."""
        if self.dry_run or not self.client:
            return False
        try:
            res = self.client.request(
                path=f"databases/{self.cfg.notion_database_id}/query",
                method="POST",
                body={
                    "filter": {"property": PROPS["message_id"],
                               "rich_text": {"equals": message_id}},
                    "page_size": 1,
                },
            )
            return bool(res.get("results"))
        except Exception as exc:  # a failed lookup must not block ingestion
            log.warning("Duplicate check failed (%s), continuing", exc)
            return False

    def create_ticket(self, item: IncomingEmail, result: TriageResult) -> dict:
        if self.dry_run:
            log.info("[dry-run] would create: %s | %s | %s",
                     result.category, result.priority, item.subject[:60])
            return {"id": "dry-run", "url": None}
        return self._create(item, result)

    @retry(attempts=4, base_delay=2, max_delay=30)
    def _create(self, item: IncomingEmail, result: TriageResult) -> dict:
        page = self.client.pages.create(
            parent={"database_id": self.cfg.notion_database_id},
            properties=build_properties(item, result),
            children=build_children(item),
        )
        log.info("Notion page created: %s", page.get("url"))
        return page


def record_failure(item: IncomingEmail, result: TriageResult | None, error: Exception,
                   cfg: Config = config) -> None:
    """Never lose an email: append it to a replayable failed queue."""
    entry = {
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": f"{type(error).__name__}: {error}",
        "email": item.to_dict(),
        "triage": result.to_dict() if result else None,
    }
    path: Path = cfg.failed_queue_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.error("Recorded failure for uid=%s in %s", item.uid, path.name)
