"""Reddit JSON API client - no auth, public threads only.

By default fetches via direct urllib (works locally on residential IPs).
Set USE_BROWSERLESS_FETCH=1 in the environment to route fetches through
Browserless's Playwright endpoint instead. This is needed in CI environments
(GitHub Actions, etc.) where Reddit aggressively rate-limits cloud IPs.
"""
from __future__ import annotations
import json, os, time, urllib.parse, urllib.request, urllib.error
from dataclasses import dataclass
from typing import Iterable

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# Lazy-imported only when USE_BROWSERLESS_FETCH=1 to keep direct path lightweight
_playwright_browser = None  # cached connected browser
_playwright_ctx = None
_playwright_p = None


def _use_browserless() -> bool:
    return os.environ.get("USE_BROWSERLESS_FETCH") == "1"


_browserless_session = None  # /unblock response containing browserWSEndpoint


def _get_browserless_request():
    """Return a Playwright APIRequestContext attached to a Browserless-unblocked session.

    Uses the /chromium/unblock endpoint, which goes far beyond /stealth: it
    routes through Browserless's residential proxy with sticky IP, handles
    Cloudflare/Reddit-style challenges automatically, and returns a live
    browser session with all anti-bot cookies pre-set.

    We then connect Playwright to that session's browserWSEndpoint and reuse
    it for all .json fetches in this cron run - so the session warmth from
    the initial unblock navigation is carried through.

    Without /unblock the listener gets 403 "Blocked" pages from Reddit on
    GitHub Actions runners, even with /stealth + residential routing.
    """
    global _playwright_browser, _playwright_ctx, _playwright_p, _browserless_session
    if _playwright_ctx is not None:
        return _playwright_ctx.request

    from playwright.sync_api import sync_playwright

    key = os.environ.get("BROWSERLESS_API_KEY", "")
    if not key:
        raise RuntimeError("USE_BROWSERLESS_FETCH=1 but BROWSERLESS_API_KEY missing")

    # POST /chromium/unblock to bypass Reddit's anti-bot + get a live session
    query_params = urllib.parse.urlencode({
        "token": key,
        "proxy": "residential",
        "proxySticky": "true",
        "timeout": 120000,
    })
    unblock_url = f"https://production-sfo.browserless.io/chromium/unblock?{query_params}"
    body = json.dumps({
        "url": "https://old.reddit.com/",
        "content": False,
        "cookies": False,
        "screenshot": False,
        "browserWSEndpoint": True,
        "ttl": 600000,  # 10 min - covers the full sweep
    }).encode()

    req = urllib.request.Request(
        unblock_url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    import sys
    print(f"  [browserless unblock] requesting session for old.reddit.com (this can take 30-60s)...", file=sys.stderr, flush=True)
    with urllib.request.urlopen(req, timeout=180) as r:
        _browserless_session = json.loads(r.read())

    ws_endpoint = _browserless_session.get("browserWSEndpoint")
    if not ws_endpoint:
        raise RuntimeError(f"unblock returned no browserWSEndpoint: {_browserless_session}")
    print(f"  [browserless unblock] session ready", file=sys.stderr, flush=True)

    # Connect Playwright. The query string already includes token+proxy params.
    _playwright_p = sync_playwright().start()
    _playwright_browser = _playwright_p.chromium.connect_over_cdp(f"{ws_endpoint}?{query_params}")
    _playwright_ctx = (
        _playwright_browser.contexts[0]
        if _playwright_browser.contexts
        else _playwright_browser.new_context()
    )
    return _playwright_ctx.request


def shutdown_browserless():
    """Close the cached Browserless connection + session. Call at end of listener.main()."""
    global _playwright_browser, _playwright_ctx, _playwright_p, _browserless_session
    if _playwright_browser is not None:
        try:
            _playwright_browser.close()
        except Exception:
            pass
    if _playwright_p is not None:
        try:
            _playwright_p.stop()
        except Exception:
            pass
    # The /unblock response doesn't include a stop URL - the session times out
    # naturally on ttl. Nothing to DELETE.
    global _browserless_page
    _playwright_browser = _playwright_ctx = _playwright_p = _browserless_session = _browserless_page = None


@dataclass
class RedditPost:
    id: str
    sub: str
    title: str
    selftext: str
    permalink: str
    url: str
    author: str
    created_utc: float
    score: int
    num_comments: int
    stickied: bool
    over_18: bool
    is_promoted: bool

    @classmethod
    def from_json(cls, sub: str, data: dict) -> RedditPost:
        return cls(
            id=data.get("id", ""),
            sub=sub,
            title=data.get("title", ""),
            selftext=data.get("selftext", ""),
            permalink=f"https://www.reddit.com{data.get('permalink', '')}",
            url=data.get("url", ""),
            author=data.get("author", "?"),
            created_utc=float(data.get("created_utc", 0)),
            score=int(data.get("score", 0)),
            num_comments=int(data.get("num_comments", 0)),
            stickied=bool(data.get("stickied", False)),
            over_18=bool(data.get("over_18", False)),
            is_promoted=bool(data.get("promoted", False)),
        )


def _fetch_json(url: str, timeout: int = 15) -> dict:
    if _use_browserless():
        return _fetch_json_via_browserless(url, timeout)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_url": url}
    except Exception as e:
        return {"_error": str(e), "_url": url}


_browserless_page = None  # cached page reused for all fetches


def _fetch_json_via_browserless(url: str, timeout: int = 15) -> dict:
    """Fetch Reddit JSON through the Browserless-unblocked browser session.

    Uses page.goto() (not ctx.request.get) so the fetch inherits the
    browser context's cookies, proxy, and fingerprint from the unblock
    navigation. APIRequestContext is isolated from the browser state and
    gets 403'd even with an active unblock session.

    Reddit's .json endpoints display as plain text inside a <pre> tag when
    navigated to in Chrome, so we extract that text and parse JSON.
    """
    import sys
    global _browserless_page
    try:
        _get_browserless_request()  # ensures connection + unblock are set up
    except Exception as e:
        print(f"  [browserless session create failed] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return {"_error": f"session_create: {e}", "_url": url}

    # Single retry on recoverable errors:
    #   - TargetClosedError: the page tab died mid-run (crash, navigation
    #     killed it). Recreate the page before retrying.
    #   - TimeoutError: goto exceeded the per-call timeout (slow Reddit
    #     response or proxy hiccup). Recreate the page (it may be in a
    #     weird state after partial load) and retry with double timeout.
    response = None
    for attempt in (1, 2):
        try:
            if _browserless_page is None or _browserless_page.is_closed():
                _browserless_page = _playwright_ctx.new_page()
            attempt_timeout = (timeout if attempt == 1 else timeout * 2) * 1000
            response = _browserless_page.goto(url, wait_until="domcontentloaded", timeout=attempt_timeout)
            break  # got a response (success or HTTP error - both handled below)
        except Exception as e:
            err_name = type(e).__name__
            err_str = str(e).lower()
            is_closed = ("TargetClosedError" in err_name or
                         "target closed" in err_str or
                         "page has been closed" in err_str)
            is_timeout = ("TimeoutError" in err_name or "timeout" in err_str)
            if attempt == 1 and (is_closed or is_timeout):
                reason = "page closed" if is_closed else f"timeout (retry with {timeout*2}s)"
                print(f"  [browserless {reason}, recreating] {url}", file=sys.stderr, flush=True)
                try:
                    if _browserless_page is not None and not _browserless_page.is_closed():
                        _browserless_page.close()
                except Exception:
                    pass
                _browserless_page = None
                continue
            print(f"  [browserless fetch exception] {url} {err_name}: {e}", file=sys.stderr, flush=True)
            return {"_error": f"browserless: {e}", "_url": url}

    try:
        if response is None:
            return {"_error": "no response", "_url": url}
        status = response.status
        if status >= 400:
            body_preview = ""
            try:
                body_preview = _browserless_page.content()[:200]
            except Exception:
                pass
            print(f"  [browserless HTTP {status}] {url} body={body_preview!r}", file=sys.stderr, flush=True)
            return {"_error": f"HTTP {status}", "_url": url}

        # Reddit JSON renders inside <pre> when navigated to in Chrome.
        # Try multiple extraction strategies.
        try:
            # Strategy 1: directly parse if Content-Type is JSON
            return response.json()
        except Exception:
            pass
        try:
            # Strategy 2: extract <pre> inner text
            pre_text = _browserless_page.locator("pre").first.inner_text(timeout=5000)
            return json.loads(pre_text)
        except Exception:
            pass
        try:
            # Strategy 3: body inner text (no pre wrapper)
            body_text = _browserless_page.locator("body").first.inner_text(timeout=5000)
            return json.loads(body_text)
        except Exception:
            text_preview = ""
            try:
                text_preview = _browserless_page.content()[:200]
            except Exception:
                pass
            print(f"  [browserless non-JSON] {url} body={text_preview!r}", file=sys.stderr, flush=True)
            return {"_error": "non-JSON response", "_url": url}
    except Exception as e:
        print(f"  [browserless fetch exception] {url} {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return {"_error": f"browserless: {e}", "_url": url}


def _reddit_host() -> str:
    """www.reddit.com returns 403-with-HTML to browser-context fetches even
    against .json endpoints. old.reddit.com serves JSON cleanly. Direct urllib
    works against either, but Browserless stealth sessions need old.reddit.
    """
    return "old.reddit.com" if _use_browserless() else "www.reddit.com"


def fetch_sub_posts(sub: str, endpoint: str = "new", limit: int = 25) -> list[RedditPost]:
    url = f"https://{_reddit_host()}/r/{sub}/{endpoint}.json?limit={limit}"
    data = _fetch_json(url)
    if "_error" in data:
        return []
    return [
        RedditPost.from_json(sub, c["data"])
        for c in data.get("data", {}).get("children", [])
        if c.get("data")
    ]


def sweep(subs: Iterable[str], endpoints: Iterable[str], limit: int, request_delay_s: float = 1.1) -> list[RedditPost]:
    """Sweep all subs × endpoints, dedupe by post ID."""
    seen_ids = set()
    posts: list[RedditPost] = []
    for sub in subs:
        for ep in endpoints:
            for p in fetch_sub_posts(sub, ep, limit):
                if p.id in seen_ids:
                    continue
                seen_ids.add(p.id)
                posts.append(p)
            time.sleep(request_delay_s)
    return posts


def fetch_thread_comments(permalink: str, limit: int = 50) -> list[dict]:
    """Fetch top comments for a thread to give the drafter context."""
    # permalink ends with / typically; append .json
    if _use_browserless():
        permalink = permalink.replace("//www.reddit.com/", "//old.reddit.com/")
    url = permalink.rstrip("/") + f".json?limit={limit}"
    data = _fetch_json(url)
    if not isinstance(data, list) or len(data) < 2:
        return []
    comments_root = data[1].get("data", {}).get("children", [])
    flat: list[dict] = []
    _walk_comments(comments_root, flat)
    return flat


@dataclass
class RedditProfile:
    username: str
    created_utc: float
    total_karma: int
    account_age_label: str
    is_suspended: bool


def fetch_reddit_profile(username: str) -> RedditProfile | None:
    """Reddit's /user/<name>/about.json - free, no auth, all we need."""
    if not username or username in ("[deleted]", "?"):
        return None
    url = f"https://{_reddit_host()}/user/{username}/about.json"
    data = _fetch_json(url)
    if "_error" in data:
        return None
    d = data.get("data") or {}
    if not d.get("name"):
        return None
    created = float(d.get("created_utc", 0))
    karma = int(d.get("total_karma") or (d.get("comment_karma", 0) + d.get("link_karma", 0)))
    return RedditProfile(
        username=username,
        created_utc=created,
        total_karma=karma,
        account_age_label=_age_label_from_created(created),
        is_suspended=bool(d.get("is_suspended")),
    )


def _age_label_from_created(created_utc: float) -> str:
    import time
    if not created_utc:
        return "?"
    age_days = (time.time() - created_utc) / 86400
    if age_days >= 365:
        return f"{round(age_days / 365, 1)}y"
    if age_days >= 30:
        return f"{int(age_days / 30)}mo"
    if age_days >= 7:
        return f"{int(age_days / 7)}w"
    return f"{int(age_days)}d"


def _walk_comments(children: list, out: list[dict], depth: int = 0, max_depth: int = 2, max_total: int = 15):
    for c in children:
        if len(out) >= max_total:
            return
        d = c.get("data", {})
        if c.get("kind") == "more" or not d.get("body"):
            continue
        out.append({
            "depth": depth,
            "author": d.get("author", "?"),
            "score": int(d.get("score", 0)),
            "body": d.get("body", "")[:600],
        })
        replies = d.get("replies")
        if isinstance(replies, dict) and depth < max_depth:
            _walk_comments(replies.get("data", {}).get("children", []), out, depth + 1, max_depth, max_total)
