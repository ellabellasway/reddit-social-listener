"""Claude-powered draft generator for Reddit replies."""
from __future__ import annotations
import os
from typing import Optional

from src.scorer import ScoredPost
from src.reddit_client import RedditPost, fetch_thread_comments
from src.browserless_client import ground_query_in_docs, DocsHit

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore


def build_system_prompt(persona: dict) -> str:
    """Build the Claude system prompt from config.yml's persona block.

    Keeping this as a function (not a module-level constant) means the whole
    voice/identity is configurable per-deployment - swap config.yml and you
    get a different person, company, and mention policy without touching code.
    """
    name = persona.get("name", "the poster")
    title = persona.get("title", "")
    company = persona.get("company", "our product")
    company_url = persona.get("company_url", "")
    blurb = persona.get("blurb", "").strip()
    competitors = persona.get("competitors", [])

    role = f"{name}, {title} of {company}" if title else name

    return f"""You are drafting a Reddit reply on behalf of {role}. You will paste this directly into Reddit, so write what the person should literally post.

Voice rules - follow strictly:
- First person, conversational
- Lead with the answer, no preamble
- 2-4 paragraphs, each separated by a blank line (Reddit needs this for paragraph breaks)
- No em dashes, en dashes, or arrows. Use hyphens, commas, periods
- No "it's not X, it's Y" or "isn't just X, it's Y" patterns (the room calls these AI tells)
- No filler vocabulary: leverage, seamless, robust, delve, cutting-edge, navigate, unlock, revolutionize, in today's fast-paced world
- No rule-of-three filler lists
- No bold or heavy formatting
- No bullet points unless the answer is naturally list-shaped (rare)
- Reference specific comments in the thread by username when it's useful (e.g. "RandomPantsAppear pointed at this already")
- Use specific numbers, version pins, product names when you have them

About {company} (background context only):
{blurb}
Category-adjacent products, for context if it comes up: {", ".join(competitors) if competitors else "(none configured)"}

When to mention {company} in the reply:
- Mention level YES: post is directly about a feature or problem {company} solves. Disclose with a final line like "Run {company_url or company} fwiw" or "Source: run {company_url or company}, [context]"
- Mention level SOFT: post is in the same domain but not product-specific. Mention only if it adds credibility. No URL.
- Mention level NO: technical depth or off-topic. Do not mention {company} at all.

When NOT to comment at all (signal this by returning an empty string):
- Post is spam, low effort, or off-topic
- The whole thread has already been answered in a way {name} would just be repeating

Output: just the reply text. No preamble like "Here's a draft:". Paragraph breaks must be real blank lines (\\n\\n).
"""


def _get_client() -> Optional[Anthropic]:
    if Anthropic is None:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


def _format_thread_context(scored: ScoredPost, top_comments: list[dict]) -> str:
    p = scored.post
    lines = [
        f"SUBREDDIT: r/{p.sub}",
        f"TITLE: {p.title}",
        f"OP: u/{p.author}",
        f"STATS: {p.score} upvotes, {p.num_comments} comments",
        "",
        "POST BODY:",
        p.selftext or "(link post, no body)",
        "",
    ]
    if top_comments:
        lines.append("EXISTING COMMENTS (oldest at top, indent = depth):")
        for c in top_comments[:10]:
            indent = "  " * c.get("depth", 0)
            lines.append(f"{indent}[{c['score']}↑] u/{c['author']}: {c['body'][:300]}")
        lines.append("")
    return "\n".join(lines)


def _format_docs_context(hits: list[DocsHit]) -> str:
    if not hits:
        return ""
    lines = [
        "PRODUCT DOCS GROUNDING - fetched live from your docs site (see src/browserless_client.py).",
        "Use these to keep technical claims accurate. Do not quote them verbatim.",
        "",
    ]
    for h in hits:
        lines.append(f"--- {h.title} ({h.url}) ---")
        lines.append(h.snippet)
        lines.append("")
    return "\n".join(lines)


def _docs_query_from_post(scored: ScoredPost) -> str:
    """Extract a docs search query from the scored post."""
    # Use strong-keyword hits as the most signal-rich query terms
    if scored.strong_hits:
        return " ".join(scored.strong_hits[:3])
    # Fall back to the post title without filler words
    title = scored.post.title.lower()
    for stop in ("how to", "should i", "anyone", "the", "and", "or", "for", "in", "of"):
        title = title.replace(stop, "")
    return " ".join(title.split()[:5])


def draft_reply(scored: ScoredPost, persona: dict, model: str = "claude-opus-4-7") -> str:
    """Generate a draft reply using Claude.

    For Technical-difficulty posts, retrieves docs context first (if a docs
    site is configured) so the model has grounded product knowledge before
    writing, instead of guessing at feature names.
    """
    client = _get_client()
    if client is None:
        return ""  # No API key - listener still surfaces the row, draft cell is empty

    top_comments = fetch_thread_comments(scored.post.permalink)
    thread_ctx = _format_thread_context(scored, top_comments)

    docs_ctx = ""
    if scored.difficulty == "Technical":
        query = _docs_query_from_post(scored)
        if query.strip():
            hits = ground_query_in_docs(query, max_results=2)
            docs_ctx = _format_docs_context(hits)

    user_msg = (
        f"Draft a Reddit reply for this thread.\n\n"
        f"Difficulty: {scored.difficulty}\n"
        f"Mention Product?: {scored.mention_product}\n\n"
        f"{docs_ctx}\n"
        f"{thread_ctx}\n\n"
        f"If you would skip this thread (low value or already well-answered), "
        f"output exactly: SKIP"
    )

    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=build_system_prompt(persona),
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()
    if text.upper().startswith("SKIP"):
        return ""
    return text
