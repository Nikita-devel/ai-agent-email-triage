#!/usr/bin/env python3
"""Verify the destination database has every property the writer expects.

    python -m scripts.check_notion           # report only
    python -m scripts.check_notion --repair  # add whatever is missing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CATEGORIES, PRIORITIES, config  # noqa: E402
from app.notion_writer import NOTION_API_VERSION, PROPS  # noqa: E402

CATEGORY_COLORS = ["blue", "green", "red", "yellow", "gray"]
PRIORITY_COLORS = ["gray", "blue", "orange", "red"]

EXPECTED = {
    PROPS["title"]: ("title", {"title": {}}),
    PROPS["category"]: ("select", {"select": {"options": [
        {"name": c, "color": CATEGORY_COLORS[i]} for i, c in enumerate(CATEGORIES)]}}),
    PROPS["priority"]: ("select", {"select": {"options": [
        {"name": p, "color": PRIORITY_COLORS[i]} for i, p in enumerate(PRIORITIES)]}}),
    PROPS["status"]: ("select", {"select": {"options": [
        {"name": "New", "color": "blue"},
        {"name": "In progress", "color": "yellow"},
        {"name": "Done", "color": "green"}]}}),
    PROPS["sender"]: ("rich_text", {"rich_text": {}}),
    PROPS["email"]: ("email", {"email": {}}),
    PROPS["summary"]: ("rich_text", {"rich_text": {}}),
    PROPS["deadline"]: ("date", {"date": {}}),
    PROPS["received"]: ("date", {"date": {}}),
    PROPS["language"]: ("select", {"select": {}}),
    PROPS["action"]: ("checkbox", {"checkbox": {}}),
    PROPS["confidence"]: ("number", {"number": {"format": "percent"}}),
    PROPS["message_id"]: ("rich_text", {"rich_text": {}}),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair", action="store_true", help="create missing properties")
    args = ap.parse_args()

    if not config.notion_api_key or not config.notion_database_id:
        print("NOTION_API_KEY / NOTION_DATABASE_ID missing in .env", file=sys.stderr)
        return 2

    from notion_client import Client

    client = Client(auth=config.notion_api_key, notion_version=NOTION_API_VERSION)
    db = client.databases.retrieve(database_id=config.notion_database_id)
    actual = db.get("properties", {})

    print(f"Database: {db.get('url')}")
    missing, wrong = {}, []
    for name, (kind, schema) in EXPECTED.items():
        found = actual.get(name)
        if not found:
            missing[name] = schema
            print(f"  MISSING  {name:<16} ({kind})")
        elif found.get("type") != kind:
            wrong.append((name, kind, found.get("type")))
            print(f"  WRONG    {name:<16} expected {kind}, found {found.get('type')}")
        else:
            print(f"  ok       {name:<16} {kind}")

    if wrong:
        print("\nType mismatches must be fixed by hand in Notion (or delete the database "
              "and re-run setup_notion).", file=sys.stderr)
    if not missing:
        print("\nSchema complete." if not wrong else "")
        return 1 if wrong else 0
    if not args.repair:
        print(f"\n{len(missing)} property/properties missing. Re-run with --repair to add them.")
        return 1

    client.databases.update(database_id=config.notion_database_id, properties=missing)
    print(f"\nAdded {len(missing)} property/properties.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
