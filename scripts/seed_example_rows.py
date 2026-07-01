"""Seed the master sheet with a couple of example rows.

Run once after the sheet is created and the Apps Script web app is deployed,
just to confirm end-to-end writes work with real-shaped data. Edit ROWS
below or delete this script once you trust the live cron.

Usage:
    python scripts/seed_example_rows.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.sheet_writer import SheetRow, post_rows


ROWS = [
    SheetRow(
        status="New", owner="", difficulty="Easy", mention_product="No",
        sub="r/webscraping",
        title="Example row - what's the best way to render JS-heavy pages at scale?",
        url="https://www.reddit.com/r/webscraping/comments/example/",
        age="1d", upvotes=5, comments=2,
        op="u/example-user", op_signals="",
        score=42.0,
        summary="Placeholder row to confirm the sheet-writer pipeline works end to end.",
        suggested_draft=(
            "This is a placeholder draft. Replace scripts/seed_example_rows.py's "
            "ROWS with real threads once you're ready, or delete this file - it's "
            "only here to prove the sheet write path works before the cron takes over."
        ),
        posted_url="", notes="Seeded by scripts/seed_example_rows.py - safe to delete.",
    ),
]


def main():
    load_dotenv()
    print(f"Seeding {len(ROWS)} example row(s) to master sheet")
    result = post_rows(ROWS)
    print(f"Result: {result}")


if __name__ == "__main__":
    main()
