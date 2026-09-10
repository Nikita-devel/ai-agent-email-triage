#!/usr/bin/env python3
"""Measure classification accuracy against the labelled fixtures.

    python -m scripts.evaluate            # uses LLM_PROVIDER from .env
    python -m scripts.evaluate --mock     # force the offline heuristic
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import MockProvider, get_provider  # noqa: E402
from app.main import load_fixture_emails  # noqa: E402
import json  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sample_emails.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    expected = {f"<{it['id']}@demo.fixture>": it["expected"]
                for it in json.loads(FIXTURES.read_text(encoding="utf-8"))}
    provider = MockProvider() if args.mock else get_provider()
    emails = load_fixture_emails()

    cat_hits = prio_hits = deadline_hits = 0
    confusion: dict[str, Counter] = defaultdict(Counter)
    rows = []

    for item in emails:
        exp = expected[item.message_id]
        res = provider.classify(item)
        cat_ok = res.category == exp["category"]
        prio_ok = res.priority == exp["priority"]
        dl_ok = bool(res.extracted_deadline) == exp["has_deadline"]
        cat_hits += cat_ok
        prio_hits += prio_ok
        deadline_hits += dl_ok
        confusion[exp["category"]][res.category] += 1
        rows.append((item.uid, exp["category"], res.category, cat_ok,
                     exp["priority"], res.priority, prio_ok))

    n = len(emails)
    print(f"{'id':<14}{'expected':<18}{'predicted':<18}{'':<3}{'exp prio':<10}{'pred prio':<10}")
    print("-" * 76)
    for uid, ec, pc, cok, ep, pp, pok in rows:
        print(f"{uid:<14}{ec:<18}{pc:<18}{'OK' if cok else 'XX':<3}{ep:<10}{pp:<10}{'' if pok else '<-'}")
    print("-" * 76)
    print(f"category accuracy : {cat_hits}/{n}  ({cat_hits / n:.0%})")
    print(f"priority accuracy : {prio_hits}/{n}  ({prio_hits / n:.0%})")
    print(f"deadline detection: {deadline_hits}/{n}  ({deadline_hits / n:.0%})")

    print("\nconfusion (expected -> predicted):")
    for exp_cat, counter in confusion.items():
        detail = ", ".join(f"{k}={v}" for k, v in counter.most_common())
        print(f"  {exp_cat:<18} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
