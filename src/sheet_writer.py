"""POSTs new rows to an Apps Script web app that appends to the master sheet.

We use Apps Script instead of the Google Sheets API + Service Account because:
- Apps Script web apps avoid the Service Account / OAuth dance
- The sheet owner controls access via standard Drive permissions
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class SheetRow:
    status: str
    owner: str
    difficulty: str
    mention_product: str
    sub: str
    title: str
    url: str
    age: str
    upvotes: int
    comments: int
    op: str
    op_signals: str
    score: float
    summary: str
    suggested_draft: str
    posted_url: str
    notes: str

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "owner": self.owner,
            "difficulty": self.difficulty,
            "mention_product": self.mention_product,
            "sub": self.sub,
            "title": self.title,
            "url": self.url,
            "age": self.age,
            "upvotes": self.upvotes,
            "comments": self.comments,
            "op": self.op,
            "op_signals": self.op_signals,
            "score": self.score,
            "summary": self.summary,
            "suggested_draft": self.suggested_draft,
            "posted_url": self.posted_url,
            "notes": self.notes,
        }


def post_rows(rows: list[SheetRow]) -> dict:
    url = os.environ.get("SHEET_WRITER_URL", "")
    token = os.environ.get("SHEET_WRITER_TOKEN", "")
    if not url:
        raise RuntimeError("SHEET_WRITER_URL not set")
    payload = json.dumps({
        "token": token,
        "rows": [r.to_dict() for r in rows],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}
