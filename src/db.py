"""SQLite seen-list cache so we don't re-surface posts."""
from __future__ import annotations
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent.parent / "listener.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    id TEXT PRIMARY KEY,
    sub TEXT,
    surfaced_at INTEGER
);

CREATE TABLE IF NOT EXISTS profile_cache (
    username TEXT PRIMARY KEY,
    account_age_label TEXT,
    karma INTEGER,
    cached_at INTEGER
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DEFAULT_PATH
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def is_seen(conn: sqlite3.Connection, post_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_posts WHERE id = ?", (post_id,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, post_id: str, sub: str, ts: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_posts (id, sub, surfaced_at) VALUES (?, ?, ?)",
        (post_id, sub, ts),
    )
    conn.commit()


def get_cached_profile(conn: sqlite3.Connection, username: str, max_age_s: int = 7 * 86400) -> dict | None:
    row = conn.execute(
        "SELECT account_age_label, karma, cached_at FROM profile_cache WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    age_label, karma, cached_at = row
    import time
    if time.time() - cached_at > max_age_s:
        return None
    return {"account_age_label": age_label, "karma": karma}


def cache_profile(conn: sqlite3.Connection, username: str, age_label: str, karma: int | None, ts: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO profile_cache (username, account_age_label, karma, cached_at) VALUES (?, ?, ?, ?)",
        (username, age_label, karma, ts),
    )
    conn.commit()
