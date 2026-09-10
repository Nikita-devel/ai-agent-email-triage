#!/usr/bin/env python3
"""One-off: create the Notion database the agent writes into.

Prerequisite: create an internal integration at notion.so/my-integrations,
put its secret in NOTION_API_KEY, then share a Notion page with that
integration and pass the page id here.

    python -m scripts.setup_notion --parent-page <page_id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CATEGORIES, PRIORITIES, config  # noqa: E402
from app.notion_writer import PROPS  # noqa: E402

CATEGORY_COLORS = ["blue", "green", "red", "yellow", "gray"]
PRIORITY_COLORS = ["gray", "blue", "orange", "red"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-page", required=True, help="Notion page id shared with the integration")
    ap.add_argument("--title", default="Inbound Requests (AI triage)")
    args = ap.parse_args()

    if not config.notion_api_key:
        print("NOTION_API_KEY is empty", file=sys.stderr)
        return 2

    from notion_client import Client

    client = Client(auth=config.notion_api_key)
    db = client.databases.create(
        parent={"type": "page_id", "page_id": args.parent_page},
        title=[{"type": "text", "text": {"content": args.title}}],
        properties={
            PROPS["title"]: {"title": {}},
            PROPS["category"]: {"select": {"options": [
                {"name": c, "color": CATEGORY_COLORS[i]} for i, c in enumerate(CATEGORIES)]}},
            PROPS["priority"]: {"select": {"options": [
                {"name": p, "color": PRIORITY_COLORS[i]} for i, p in enumerate(PRIORITIES)]}},
            PROPS["status"]: {"select": {"options": [
                {"name": "New", "color": "blue"},
                {"name": "In progress", "color": "yellow"},
                {"name": "Done", "color": "green"}]}},
            PROPS["sender"]: {"rich_text": {}},
            PROPS["email"]: {"email": {}},
            PROPS["summary"]: {"rich_text": {}},
            PROPS["deadline"]: {"date": {}},
            PROPS["received"]: {"date": {}},
            PROPS["language"]: {"select": {}},
            PROPS["action"]: {"checkbox": {}},
            PROPS["confidence"]: {"number": {"format": "percent"}},
            PROPS["message_id"]: {"rich_text": {}},
        },
    )
    print("Database created.")
    print(f"  URL: {db.get('url')}")
    print(f"  Put this in .env ->  NOTION_DATABASE_ID={db['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
