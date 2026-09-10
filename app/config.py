"""Central configuration, loaded once from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CATEGORIES = (
    "technical_support",
    "sales_inquiry",
    "complaint",
    "administrative",
    "other",
)
PRIORITIES = ("low", "medium", "high", "urgent")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    imap_host: str = os.getenv("IMAP_HOST", "imap.gmail.com")
    imap_port: int = _int("IMAP_PORT", 993)
    imap_user: str = os.getenv("IMAP_USER", "")
    imap_password: str = os.getenv("IMAP_PASSWORD", "")
    imap_folder: str = os.getenv("IMAP_FOLDER", "INBOX")
    imap_mark_seen: bool = _bool("IMAP_MARK_SEEN", True)

    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _int("SMTP_PORT", 587)

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-5")

    notion_api_key: str = os.getenv("NOTION_API_KEY", "")
    notion_database_id: str = os.getenv("NOTION_DATABASE_ID", "")

    poll_interval: int = _int("POLL_INTERVAL_SECONDS", 60)
    max_emails_per_run: int = _int("MAX_EMAILS_PER_RUN", 20)
    dry_run: bool = _bool("DRY_RUN", False)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    data_dir: Path = field(default=ROOT / "data")

    @property
    def failed_queue_path(self) -> Path:
        return self.data_dir / "failed_queue.jsonl"

    @property
    def processed_log_path(self) -> Path:
        return self.data_dir / "processed.jsonl"

    def require_imap(self) -> None:
        missing = [k for k, v in {"IMAP_USER": self.imap_user,
                                  "IMAP_PASSWORD": self.imap_password}.items() if not v]
        if missing:
            raise ConfigError(f"Missing env vars: {', '.join(missing)}")

    def require_llm(self) -> None:
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ConfigError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty")
        if self.llm_provider not in {"anthropic", "mock"}:
            raise ConfigError(f"Unknown LLM_PROVIDER: {self.llm_provider}")

    def require_notion(self) -> None:
        if self.dry_run:
            return
        missing = [k for k, v in {"NOTION_API_KEY": self.notion_api_key,
                                  "NOTION_DATABASE_ID": self.notion_database_id}.items() if not v]
        if missing:
            raise ConfigError(f"Missing env vars: {', '.join(missing)} (or set DRY_RUN=true)")


config = Config()
config.data_dir.mkdir(exist_ok=True)
