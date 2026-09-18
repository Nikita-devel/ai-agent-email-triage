"""Gmail API ingestion - OAuth instead of an App Password.

Scopes: gmail.modify (read + mark as read) and gmail.insert (seeding fixtures).
The first run opens a browser once and caches a refresh token in token.json.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime

from .config import Config, config
from .email_reader import IncomingEmail, parse_message

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.insert",
]

# Gmail's own labels; anything else in GMAIL_LABEL is created on demand so the
# agent reads a dedicated demo label instead of the whole mailbox.
SYSTEM_LABELS = {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SENT", "DRAFT",
                 "SPAM", "TRASH", "CATEGORY_PERSONAL", "CATEGORY_SOCIAL",
                 "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}


def _exec(request, attempts: int = 5):
    """Gmail bills per-request quota units; back off on 403/429 instead of dying."""
    from googleapiclient.errors import HttpError

    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status not in (403, 429) or attempt == attempts:
                raise
            log.warning("Gmail rate limit, retrying in %.0fs (%d/%d)", delay, attempt, attempts)
            time.sleep(delay)
            delay *= 2


def _build_service(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if cfg.gmail_token_path.exists():
        creds = Credentials.from_authorized_user_file(str(cfg.gmail_token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(cfg.gmail_credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        cfg.gmail_token_path.write_text(creds.to_json(), encoding="utf-8")
        cfg.gmail_token_path.chmod(0o600)
        log.info("OAuth token stored in %s", cfg.gmail_token_path.name)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailReader:
    """Same interface as ImapReader, backed by the Gmail REST API."""

    name = "gmail_api"

    def __init__(self, cfg: Config = config) -> None:
        cfg.require_gmail()
        self.cfg = cfg
        self.service = _build_service(cfg)
        self.label_id = self._resolve_label(cfg.gmail_label)

    def _resolve_label(self, name: str) -> str:
        """Return the label id, creating a user label the first time it is used."""
        if name.upper() in SYSTEM_LABELS:
            return name.upper()
        existing = _exec(self.service.users().labels().list(userId="me")).get("labels", [])
        for label in existing:
            if label["name"] == name:
                return label["id"]
        created = _exec(self.service.users().labels().create(
            userId="me",
            body={"name": name,
                  "labelListVisibility": "labelShow",
                  "messageListVisibility": "show"},
        ))
        log.info("Created Gmail label %r", name)
        return created["id"]

    def fetch_unseen(self, limit: int | None = None) -> list[IncomingEmail]:
        limit = limit or self.cfg.max_emails_per_run
        listing = _exec(self.service.users().messages().list(
            userId="me",
            labelIds=[self.label_id, "UNREAD"],
            maxResults=limit,
        ))
        ids = [m["id"] for m in listing.get("messages", [])]
        log.info("Gmail: %d unread message(s) to process", len(ids))

        out: list[IncomingEmail] = []
        for msg_id in ids:
            msg = _exec(self.service.users().messages().get(
                userId="me", id=msg_id, format="raw"
            ))
            raw = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
            out.append(parse_message(msg_id, raw))  # uid == Gmail message id
        return out

    def mark_seen(self, uid: str) -> None:
        if not self.cfg.imap_mark_seen:
            return
        _exec(self.service.users().messages().modify(
            userId="me", id=uid, body={"removeLabelIds": ["UNREAD"]}
        ))

    def append(self, raw: bytes, internal_date: datetime) -> str:
        """messages.insert puts the message in the mailbox without sending it."""
        labels = {self.label_id, "UNREAD", "INBOX"}
        body = {
            "raw": base64.urlsafe_b64encode(raw).decode("ascii"),
            "labelIds": sorted(labels),
        }
        _exec(self.service.users().messages().insert(
            userId="me", body=body, internalDateSource="dateHeader"))
        return "OK"

    def _seeded_ids(self, header_name: str, header_value: str) -> list[str]:
        """Gmail search does not index custom headers - match on metadata instead."""
        ids: list[str] = []
        page_token = None
        while True:
            listing = _exec(self.service.users().messages().list(
                userId="me", labelIds=[self.label_id],
                maxResults=100, pageToken=page_token,
            ))
            for stub in listing.get("messages", []):
                msg = _exec(self.service.users().messages().get(
                    userId="me", id=stub["id"], format="metadata",
                    metadataHeaders=[header_name],
                ))
                headers = msg.get("payload", {}).get("headers", [])
                if any(h["name"].lower() == header_name.lower()
                       and header_value in h["value"] for h in headers):
                    ids.append(stub["id"])
            page_token = listing.get("nextPageToken")
            if not page_token:
                break
        return ids

    def purge(self, header_name: str, header_value: str) -> int:
        ids = self._seeded_ids(header_name, header_value)
        for msg_id in ids:
            _exec(self.service.users().messages().trash(userId="me", id=msg_id))
        return len(ids)

    def reset_unread(self, header_name: str, header_value: str) -> int:
        """Put the seeded fixtures back to UNREAD - lets the demo be re-recorded."""
        ids = self._seeded_ids(header_name, header_value)
        for msg_id in ids:
            _exec(self.service.users().messages().modify(
                userId="me", id=msg_id, body={"addLabelIds": ["UNREAD"]}
            ))
        return len(ids)
