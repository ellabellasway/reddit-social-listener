"""Main listener entry point.

Run hourly via cron (or GitHub Actions). Sweeps Reddit, scores, classifies,
optionally drafts a reply, appends to the master sheet.

Usage:
    python listener.py             # full run
    python listener.py --dry-run   # don't write to sheet, print rows
    python listener.py --no-draft  # skip Claude drafting
    python listener.py --no-profile # skip Browserless OP profile lookup
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Make `from src...` work whether run as `python listener.py` or as a module
sys.path.insert(0, str(Path(__file__).parent))

from src.reddit_client import sweep, fetch_reddit_profile, shutdown_browserless  # noqa: E402
from src.scorer import score_post, passes_filters, age_label, ScoredPost  # noqa: E402
from src.db import connect, is_seen, mark_seen, get_cached_profile, cache_profile  # noqa: E402
from src.drafter import draft_reply  # noqa: E402
from src.sheet_writer import SheetRow, post_rows  # noqa: E402
from src.slack_notifier import post_alerts  # noqa: E402


def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def get_op_signals(conn, username: str, skip_browserless: bool = False) -> tuple[str, int | None]:
    """Return (op_signals_label, account_age_days or None).

    Uses SQLite cache + Browserless lookup. Returns empty label and None on
    miss/failure so the row still gets surfaced.
    """
    if not username or username in ("[deleted]", "?"):
        return "", None
    cached = get_cached_profile(conn, username)
    if cached:
        label = _signal_label(cached.get("account_age_label", ""), cached.get("karma"))
        days = _label_to_days(cached.get("account_age_label", ""))
        return label, days
    if skip_browserless:
        return "", None
    profile = fetch_reddit_profile(username)
    if profile is None:
        return "", None
    cache_profile(conn, username, profile.account_age_label, profile.total_karma, int(time.time()))
    days = int((time.time() - profile.created_utc) / 86400) if profile.created_utc else None
    return (
        _signal_label(profile.account_age_label, profile.total_karma),
        days,
    )


def _signal_label(age_label_str: str, karma: int | None) -> str:
    karma_str = f"{karma:,}k" if karma and karma >= 1000 else (str(karma) if karma else "?")
    age_str = age_label_str or "?"
    return f"{age_str} · {karma_str}"


def _label_to_days(age_label_str: str) -> int | None:
    import re
    m = re.match(r"(\d+)(y|mo|w|d|h|m)$", age_label_str)
    if not m:
        return None
    n = int(m.group(1))
    return {
        "y": n * 365, "mo": n * 30, "w": n * 7,
        "d": n, "h": 0, "m": 0,
    }.get(m.group(2))


def filter_by_op(scored: ScoredPost, conn, cfg: dict, skip_browserless: bool) -> tuple[bool, str, int | None]:
    """Return (passes, op_signals_label, days)."""
    op_filters = cfg.get("op_filters", {})
    min_days = op_filters.get("min_account_age_days", 30)
    min_karma = op_filters.get("min_karma", 100)
    signals, days = get_op_signals(conn, scored.post.author, skip_browserless)

    if days is None:
        # If we couldn't get profile data, surface the post but flag in notes
        return True, signals, None

    if days < min_days:
        return False, signals, days

    # We don't have karma in the days return path; pull from cache
    cached = get_cached_profile(conn, scored.post.author)
    karma = cached.get("karma") if cached else None
    if karma is not None and karma < min_karma:
        return False, signals, days

    return True, signals, days


def build_row(scored: ScoredPost, op_signals: str, draft_text: str, notes: str = "") -> SheetRow:
    p = scored.post
    return SheetRow(
        status="New",
        owner="",
        difficulty=scored.difficulty,
        mention_product=scored.mention_product,
        sub=f"r/{p.sub}",
        title=p.title,
        url=p.permalink,
        age=age_label(p.created_utc),
        upvotes=p.score,
        comments=p.num_comments,
        op=f"u/{p.author}",
        op_signals=op_signals,
        score=scored.fit_score,
        summary=scored.summary,
        suggested_draft=draft_text,
        posted_url="",
        notes=notes,
    )


def main():
    load_dotenv()
    cfg = load_config()
    try:
        _main_inner(cfg)
    finally:
        shutdown_browserless()


def _main_inner(cfg: dict):

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't write to sheet, print rows instead")
    parser.add_argument("--no-draft", action="store_true", help="Skip Claude drafting")
    parser.add_argument("--no-profile", action="store_true", help="Skip Browserless OP profile lookup")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of new rows surfaced")
    args = parser.parse_args()

    print(f"[{datetime.now(timezone.utc).isoformat()}] Reddit listener starting")
    print(f"  subs: {len(cfg['subs'])}, endpoints: {cfg['endpoints']}, limit/ep: {cfg['limit']}")

    conn = connect()
    posts = sweep(cfg["subs"], cfg["endpoints"], cfg["limit"])
    print(f"  fetched {len(posts)} posts across all subs")

    new_rows: list[SheetRow] = []
    surfaced = 0
    for p in posts:
        if is_seen(conn, p.id):
            continue
        scored = score_post(
            p,
            strong=cfg["strong_keywords"],
            medium=cfg["medium_keywords"],
            pain=cfg["pain_keywords"],
            technical_terms=cfg.get("technical_terms", []),
            soft_mention_keywords=cfg.get("soft_mention_keywords", []),
        )
        if not passes_filters(scored, cfg["min_score"], cfg["window_days"]):
            continue
        passes_op, op_signals, _days = filter_by_op(scored, conn, cfg, args.no_profile)
        if not passes_op:
            print(f"  skipping (OP filter): r/{p.sub} u/{p.author} - {p.title[:60]}")
            mark_seen(conn, p.id, p.sub, int(time.time()))
            continue

        draft_text = ""
        notes = ""
        if not args.no_draft:
            try:
                draft_text = draft_reply(scored, cfg.get("persona", {}), cfg.get("voice_rules"))
                if not draft_text:
                    notes = "Drafter returned empty (likely SKIP). Surfacing for human review."
            except Exception as e:
                notes = f"Draft generation failed: {str(e)[:120]}"

        row = build_row(scored, op_signals, draft_text, notes)
        new_rows.append(row)
        mark_seen(conn, p.id, p.sub, int(time.time()))
        surfaced += 1
        print(f"  + {scored.difficulty:<9} [score {scored.fit_score:>5}] r/{p.sub} - {p.title[:70]}")

        if args.limit and surfaced >= args.limit:
            break

    print(f"  surfaced: {len(new_rows)} new rows")

    if not new_rows:
        print("  nothing to write. exiting clean.")
        return

    if args.dry_run:
        print("  --dry-run: skipping sheet write. Sample row:")
        from pprint import pprint
        pprint(new_rows[0].to_dict())
        return

    result = post_rows(new_rows)
    print(f"  sheet write result: {result}")

    if not result.get("error"):
        sent = post_alerts(
            new_rows,
            threshold=cfg.get("alert_threshold", 70),
            sheet_url=cfg.get("sheet_url", ""),
        )
        if sent:
            print(f"  slack alerts sent: {sent}")


if __name__ == "__main__":
    main()
