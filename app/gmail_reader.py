"""Gmail API ingestion - OAuth instead of an App Password.

Scopes: gmail.modify (read + mark as read) and gmail.insert (seeding fixtures).
The first run opens a browser once and caches a refresh token in token.json.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime

from .config import Config, config
from .email_reader import IncomingEmail, parse_message

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.insert",
]


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

    def fetch_unseen(self, limit: int | None = None) -> list[IncomingEmail]:
        limit = limit or self.cfg.max_emails_per_run
        listing = self.service.users().messages().list(
            userId="me",
            labelIds=[self.cfg.gmail_label, "UNREAD"],
            maxResults=limit,
        ).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        log.info("Gmail: %d unread message(s) to process", len(ids))

        out: list[IncomingEmail] = []
        for msg_id in ids:
            msg = self.service.users().messages().get(
                userId="me", id=msg_id, format="raw"
            ).execute()
            raw = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
            out.append(parse_message(msg_id, raw))  # uid == Gmail message id
        return out

    def mark_seen(self, uid: str) -> None:
        if not self.cfg.imap_mark_seen:
            return
        self.service.users().messages().modify(
            userId="me", id=uid, body={"removeLabelIds": ["UNREAD"]}
        ).execute()

    def append(self, raw: bytes, internal_date: datetime) -> str:
        """messages.insert puts the message in the mailbox without sending it."""
        body = {
            "raw": base64.urlsafe_b64encode(raw).decode("ascii"),
            "labelIds": [self.cfg.gmail_label, "UNREAD"],
            "internalDateSource": "dateHeader",
        }
        self.service.users().messages().insert(userId="me", body=body).execute()
        return "OK"

    def purge(self, header_name: str, header_value: str) -> int:
        listing = self.service.users().messages().list(
            userId="me", q=f'"{header_value}"', maxResults=100
        ).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        for msg_id in ids:
            self.service.users().messages().trash(userId="me", id=msg_id).execute()
        return len(ids)
