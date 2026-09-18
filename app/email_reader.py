"""IMAP ingestion: fetch unread messages from the demo mailbox.

Only ever point this at a dedicated test mailbox.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator

from .config import Config, config

log = logging.getLogger(__name__)

MAX_BODY_CHARS = 6000


@dataclass
class IncomingEmail:
    uid: str
    message_id: str
    sender_name: str
    sender_email: str
    subject: str
    body: str
    received_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:  # malformed header - fall back to raw
        return value.strip()


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to a stripped text/html part."""
    plain, html = [], []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():  # attachment
                continue
            ctype = part.get_content_type()
            if ctype not in {"text/plain", "text/html"}:
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            (plain if ctype == "text/plain" else html).append(text)
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        (plain if msg.get_content_type() == "text/plain" else html).append(text)

    body = "\n".join(plain).strip() or _html_to_text("\n".join(html)).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body[:MAX_BODY_CHARS]


def parse_message(uid: str, raw: bytes) -> IncomingEmail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(msg.get("From", ""))
    try:
        received = parsedate_to_datetime(msg.get("Date")).isoformat()
    except Exception:
        received = None
    return IncomingEmail(
        uid=uid,
        message_id=_decode(msg.get("Message-ID")) or f"uid-{uid}",
        sender_name=_decode(name) or addr.split("@")[0],
        sender_email=addr.lower(),
        subject=_decode(msg.get("Subject")) or "(no subject)",
        body=extract_body(msg),
        received_at=received,
    )


@contextmanager
def imap_connection(cfg: Config = config) -> Iterator[imaplib.IMAP4_SSL]:
    cfg.require_imap()
    conn = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        conn.login(cfg.imap_user, cfg.imap_password)
        conn.select(cfg.imap_folder)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


def fetch_unseen(cfg: Config = config, limit: int | None = None) -> list[IncomingEmail]:
    """Return unread messages without marking them seen (BODY.PEEK)."""
    limit = limit or cfg.max_emails_per_run
    out: list[IncomingEmail] = []
    with imap_connection(cfg) as conn:
        status, data = conn.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            log.error("IMAP SEARCH failed: %s", status)
            return out
        uids = data[0].split()[:limit]
        log.info("Found %d unseen message(s), processing %d", len(data[0].split()), len(uids))
        for raw_uid in uids:
            uid = raw_uid.decode()
            status, payload = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                log.warning("Could not fetch uid=%s", uid)
                continue
            out.append(parse_message(uid, payload[0][1]))
    return out


def mark_seen(uid: str, cfg: Config = config) -> None:
    """Flag a message as processed - called only after a successful write."""
    if not cfg.imap_mark_seen:
        return
    with imap_connection(cfg) as conn:
        conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")


def mark_seen_bulk(uids: list[str], cfg: Config = config) -> None:
    if not uids or not cfg.imap_mark_seen:
        return
    with imap_connection(cfg) as conn:
        conn.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")


class ImapReader:
    """Reader interface over IMAP - see app.ingest.get_reader()."""

    name = "imap"

    def __init__(self, cfg: Config = config) -> None:
        cfg.require_imap()
        self.cfg = cfg

    def fetch_unseen(self, limit: int | None = None) -> list[IncomingEmail]:
        return fetch_unseen(self.cfg, limit)

    def mark_seen(self, uid: str) -> None:
        mark_seen(uid, self.cfg)

    def append(self, raw: bytes, internal_date) -> str:
        """Insert a message into the mailbox without sending it (used by the seeder)."""
        import imaplib as _imaplib
        import time as _time

        with imap_connection(self.cfg) as conn:
            status, _ = conn.append(
                self.cfg.imap_folder,
                "",
                _imaplib.Time2Internaldate(_time.mktime(internal_date.timetuple())),
                raw,
            )
        return status

    def purge(self, header_name: str, header_value: str) -> int:
        with imap_connection(self.cfg) as conn:
            status, data = conn.uid("SEARCH", None, "HEADER", header_name, header_value)
            uids = data[0].split() if status == "OK" else []
            for uid in uids:
                conn.uid("STORE", uid.decode(), "+FLAGS", "(\\Deleted)")
            conn.expunge()
        return len(uids)
