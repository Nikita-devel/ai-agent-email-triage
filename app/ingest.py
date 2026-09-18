"""Pick the ingestion backend declared by INGESTION in .env."""
from __future__ import annotations

import logging
from typing import Protocol

from .config import Config, ConfigError, config
from .email_reader import IncomingEmail

log = logging.getLogger(__name__)


class Reader(Protocol):
    name: str

    def fetch_unseen(self, limit: int | None = ...) -> list[IncomingEmail]: ...
    def mark_seen(self, uid: str) -> None: ...


def get_reader(cfg: Config = config) -> Reader:
    cfg.require_ingestion()
    if cfg.ingestion == "gmail_api":
        from .gmail_reader import GmailReader

        log.info("Ingestion: Gmail API (OAuth)")
        return GmailReader(cfg)
    from .email_reader import ImapReader

    log.info("Ingestion: IMAP (%s)", cfg.imap_host)
    return ImapReader(cfg)
