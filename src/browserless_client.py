"""Browserless integration.

This template uses Browserless (browserless.io) as the browser-rendering
backend for two things: fetching Reddit JSON reliably from cloud IPs (see
reddit_client.py's USE_BROWSERLESS_FETCH path) and, here, grounding
Technical-difficulty replies in your own product's docs before drafting -
BrowserQL renders your live docs pages instead of the model guessing at
feature names from stale training data.

Set DOCS_SITEMAP_URL below (or the DOCS_SITEMAP_URL env var) to your own
docs site's sitemap.xml. Swap in a different render backend by reimplementing
_run_browserql() / fetch_doc_content() - the rest of the module only depends
on that function's return shape.

Flow:
1. list_docs_urls() - fetch sitemap.xml once, cache for 24h
2. search_docs(query) - keyword-rank URLs by path-token overlap
3. fetch_doc_content(url) - BrowserQL renders the page, extracts h1 + main text
4. drafter.draft_reply() calls these and includes results in the Claude prompt
"""
from __future__ import annotations
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_BROWSERLESS_BASE_URL = "https://production-sfo.browserless.io"


def _browserless_base_url() -> str:
    """Browserless host, overridable with BROWSERLESS_BASE_URL (self-hosted or a
    non-SFO region). Read at call time, not import time, so a value in .env
    applies - load_dotenv() runs after these modules are imported."""
    return os.environ.get("BROWSERLESS_BASE_URL", DEFAULT_BROWSERLESS_BASE_URL).rstrip("/")


DOCS_SITEMAP = os.environ.get("DOCS_SITEMAP_URL", "")
DOCS_CACHE_PATH = Path(__file__).parent.parent / ".docs_urls_cache.json"
DOCS_CACHE_TTL_S = 24 * 3600

# Generic path tokens we don't want to score on - they appear in many URLs.
STOP_TOKENS = {
    "v1", "v2", "the", "and", "or", "a", "an", "to", "in", "of", "for",
    "start", "overview", "docs",
}

# Optional boost: map a Reddit query term to one or more canonical docs pages,
# for cases where path-token scoring alone would split votes across many
# pages. Empty by default - fill in with your own docs' canonical URLs for
# your most-asked-about features, e.g.:
#   "pricing": ["https://yourproduct.example.com/docs/pricing"],
QUERY_HINTS: dict[str, list[str]] = {}


@dataclass
class DocsHit:
    title: str
    url: str
    snippet: str   # populated by fetch_doc_content()


def _api_key() -> str:
    key = os.environ.get("BROWSERLESS_API_KEY", "")
    if not key:
        raise RuntimeError("BROWSERLESS_API_KEY not set")
    return key


def _run_browserql(query: str, variables: Optional[dict] = None, timeout: int = 30) -> dict:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    url = f"{_browserless_base_url()}/chrome/bql?token={urllib.parse.quote(_api_key())}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"_error": str(e)}


def list_docs_urls() -> list[str]:
    """Fetch your docs site's sitemap, return all canonical doc URLs.

    Cached on disk for 24h. The sitemap is plain XML (no JS) so we fetch it
    with a simple HTTP request; no browser render needed for the index.
    Returns [] (feature is a no-op) if DOCS_SITEMAP_URL isn't set.
    """
    if not DOCS_SITEMAP:
        return []

    if DOCS_CACHE_PATH.exists():
        try:
            cached = json.loads(DOCS_CACHE_PATH.read_text())
            if time.time() - cached.get("ts", 0) < DOCS_CACHE_TTL_S:
                return cached.get("urls", [])
        except Exception:
            pass

    req = urllib.request.Request(
        DOCS_SITEMAP,
        headers={"User-Agent": "reddit-social-listener/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception:
        return []

    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    urls = [u for u in urls if not u.endswith("/")]
    urls = sorted(set(urls))

    DOCS_CACHE_PATH.write_text(json.dumps({"ts": time.time(), "urls": urls}))
    return urls


def _tokens_from_url(url: str) -> set[str]:
    """Pull keyword tokens out of the URL path (excluding stop words)."""
    path = urllib.parse.urlparse(url).path.strip("/").lower()
    parts = re.split(r"[/_\-]+", path)
    return {p for p in parts if p and p not in STOP_TOKENS and len(p) >= 3}


def _query_tokens(query: str) -> set[str]:
    q = re.sub(r"[^a-z0-9\s]", " ", query.lower())
    return {t for t in q.split() if t and t not in STOP_TOKENS and len(t) >= 3}


def search_docs(query: str, max_results: int = 3) -> list[str]:
    """Return ranked doc URLs whose path tokens overlap with the query.

    Combines:
    - Hard-coded QUERY_HINTS (sub-stem matches) for canonical mappings
    - Sitemap-token overlap scoring as the fallback ranker
    """
    urls = list_docs_urls()
    if not urls:
        return []

    q_tokens = _query_tokens(query)
    q_lower = query.lower()

    # Start with hard-coded hint URLs that survived the sitemap fetch
    ranked: list[tuple[float, str]] = []
    seen: set[str] = set()
    url_set = set(urls)
    for hint_key, hint_urls in QUERY_HINTS.items():
        if hint_key in q_lower:
            for u in hint_urls:
                if u in url_set and u not in seen:
                    ranked.append((100.0, u))
                    seen.add(u)

    # Then sitemap-token overlap ranking
    for u in urls:
        if u in seen:
            continue
        u_tokens = _tokens_from_url(u)
        overlap = q_tokens & u_tokens
        if not overlap:
            continue
        # Score = overlap count + bonus for rare tokens (rough idf signal)
        score = float(len(overlap))
        for tok in overlap:
            # Tokens appearing in fewer than 20 URLs are rarer = better signal
            occurrences = sum(1 for x in urls if tok in _tokens_from_url(x))
            if occurrences <= 5:
                score += 1.5
        ranked.append((score, u))

    ranked.sort(reverse=True)
    return [u for _, u in ranked[:max_results]]


def fetch_doc_content(url: str, max_chars: int = 1200) -> Optional[DocsHit]:
    """Use BrowserQL to render the doc page and extract title + main text.

    Browserless renders your docs site so the drafter gets live product
    knowledge instead of a stale/guessed feature description.
    """
    query = """
mutation FetchDoc($url: String!) {
  goto(url: $url, waitUntil: networkIdle) { status }
  title: text(selector: "main h1") { text }
  body: text(selector: "main") { text }
}
"""
    result = _run_browserql(query, {"url": url}, timeout=30)
    if "_error" in result:
        return None

    data = result.get("data") or {}
    status = (data.get("goto") or {}).get("status")
    if not status or status >= 400:
        return None

    title = ((data.get("title") or {}).get("text") or "").strip()
    body = ((data.get("body") or {}).get("text") or "").strip()
    if not body:
        return None

    # Strip the "Page Not Found" content (Docusaurus 404 still returns 200)
    if "Page Not Found" in body[:50]:
        return None

    # Take the meaningful part: skip title repetition + navigation
    snippet = body
    if title and snippet.startswith(title):
        snippet = snippet[len(title):].lstrip()
    snippet = snippet.replace("\n\n\n", "\n\n")
    snippet = snippet[:max_chars]

    return DocsHit(title=title or url.rsplit("/", 1)[-1], url=url, snippet=snippet)


def ground_query_in_docs(query: str, max_results: int = 2) -> list[DocsHit]:
    """High-level helper: search + fetch content for top hits.

    Returns up to max_results DocsHit objects with snippet populated.
    """
    urls = search_docs(query, max_results=max_results * 2)
    hits: list[DocsHit] = []
    for u in urls:
        hit = fetch_doc_content(u)
        if hit:
            hits.append(hit)
        if len(hits) >= max_results:
            break
    return hits
