"""Posts a Slack alert for each newly surfaced high-score thread.

One message per thread so the team can claim it by replying in that message's
thread. Threshold and sheet link come from config; the webhook URL is a secret
read from the SLACK_WEBHOOK_URL env var. No-op (returns quietly) if the webhook
isn't set, so local runs don't need it.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error


def _draft_snippet(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if not text:
        return "_No draft yet, needs a human take._"
    snippet = text.split("\n\n")[0].strip()
    if len(snippet) > limit:
        snippet = snippet[:limit].rstrip() + "..."
    return snippet


def _blocks_for_row(row, sheet_url: str) -> list[dict]:
    score = int(round(row.score))
    title = row.title.replace("\n", " ").strip()
    header = f":fire: Reddit thread worth a reply  ·  score {score}"
    meta = f"*{row.sub}*  ·  {row.difficulty}  ·  {row.upvotes} upvotes  ·  {row.comments} comments  ·  {row.age} old"
    body = (
        f"<{row.url}|{title}>\n\n"
        f"_Summary:_ {row.summary}\n\n"
        f"_Draft starting point:_\n>{_draft_snippet(row.suggested_draft)}"
    )
    footer = (
        f"<{sheet_url}|Open the triage sheet> to grab the full draft and mark it done.\n"
        f"*Taking it?* Reply in this thread so nobody doubles up, and set Status to Drafting in the sheet."
    )
    return [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": meta}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
    ]


def _post(webhook_url: str, blocks: list[dict], fallback: str) -> bool:
    payload = json.dumps({"text": fallback, "blocks": blocks}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode().strip() == "ok"
    except urllib.error.HTTPError as e:
        print(f"  slack alert failed: HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"  slack alert failed: {e}")
        return False


def post_alerts(rows, threshold: float, sheet_url: str) -> int:
    """Post a Slack alert per row scoring >= threshold. Returns count sent."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return 0
    sent = 0
    for row in rows:
        if row.score is None or row.score < threshold:
            continue
        fallback = f"Reddit thread (score {int(round(row.score))}): {row.title}"
        if _post(webhook_url, _blocks_for_row(row, sheet_url), fallback):
            sent += 1
    return sent
