"""Scoring and difficulty classification for Reddit posts."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.reddit_client import RedditPost

Difficulty = Literal["Easy", "Medium", "Technical"]
MentionProduct = Literal["Yes", "Soft", "No"]


@dataclass
class ScoredPost:
    post: RedditPost
    fit_score: float
    difficulty: Difficulty
    mention_product: MentionProduct
    summary: str
    strong_hits: list[str]
    medium_hits: list[str]
    pain_hits: list[str]


def _count_matches(text: str, patterns: list[str]) -> tuple[int, list[str]]:
    """Case-insensitive whole-word-ish keyword count."""
    text = text.lower()
    total = 0
    hit_list: list[str] = []
    for p in patterns:
        # Build a forgiving pattern: word boundary on either side where possible
        pat = re.escape(p.lower())
        try:
            count = len(re.findall(rf"(?:^|\W){pat}(?:\W|$)", text))
        except re.error:
            continue
        if count:
            total += count
            hit_list.append(p)
    return total, hit_list


def _count_code_blocks(text: str) -> int:
    # Fenced code blocks ```...``` or ~~~ ~~~
    fenced = len(re.findall(r"```[\s\S]+?```", text))
    fenced += len(re.findall(r"~~~[\s\S]+?~~~", text))
    # 4-space indented lines (heuristic; counts each contiguous block as one)
    indented_blocks = 0
    in_block = False
    for line in text.split("\n"):
        if re.match(r"^( {4,}|\t)", line) and line.strip():
            if not in_block:
                indented_blocks += 1
                in_block = True
        else:
            in_block = False
    return fenced + indented_blocks


def _looks_like_stacktrace(text: str) -> bool:
    patterns = [
        r"Traceback \(most recent call last\)",
        r"at \S+ \(\S+:\d+:\d+\)",          # Node-style
        r"\bError:\s",
        r"\bException:\s",
        r"\sat\s\S+\.java:\d+",
        r"^\s+File \"[^\"]+\", line \d+",
    ]
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def score_fit(post: RedditPost, strong: list[str], medium: list[str], pain: list[str]) -> tuple[float, list[str], list[str], list[str]]:
    body = f"{post.title}\n{post.selftext}"
    strong_n, strong_hits = _count_matches(body, strong)
    medium_n, medium_hits = _count_matches(body, medium)
    pain_n, pain_hits = _count_matches(body, pain)
    raw = (strong_n * 3) + (medium_n * 1) + (pain_n * 2)
    raw += min(post.num_comments, 80) / 10
    raw += min(post.score, 200) / 25
    # Display 0-100: 50 raw signal = 100, anything above caps.
    # Buckets: 80-100 drop-everything, 40-79 strong, 20-39 solid, <20 marginal.
    score = min(raw * 2, 100)
    return round(score, 1), strong_hits, medium_hits, pain_hits


def classify_difficulty(post: RedditPost, technical_terms: list[str]) -> Difficulty:
    text = f"{post.title}\n{post.selftext}"
    code_blocks = _count_code_blocks(post.selftext)
    if code_blocks >= 1 or _looks_like_stacktrace(post.selftext):
        return "Technical"

    tech_term_count, _ = _count_matches(text, technical_terms)
    title_lower = post.title.lower()
    easy_patterns = [
        r"\bshould i\b",
        r"\banyone using\b",
        r"\brecommend",
        r"\bwhat'?s the deal with\b",
        r"\bworth it\b",
        r"\bwould you pay\b",
        r"\bany good\b",
        r"\bbest .* for\b",
        r"\balternative",
    ]
    is_question = any(re.search(p, title_lower) for p in easy_patterns)
    is_short_body = len(post.selftext) < 600

    if tech_term_count >= 5:
        return "Technical"
    if tech_term_count >= 2:
        return "Medium"
    if is_question and is_short_body:
        return "Easy"
    if tech_term_count <= 1 and len(post.selftext) < 1500:
        return "Easy"
    return "Medium"


def decide_mention_product(strong_hits: list[str], medium_hits: list[str], soft_mention_keywords: list[str]) -> MentionProduct:
    """Decide whether the reply should disclose/mention your product.

    - Yes: post hit one of the strong (high buyer-intent) keywords - it's
      directly about the category your product is in.
    - Soft: post only hit a medium keyword that's in soft_mention_keywords
      (config.yml) - on-topic enough for a credibility mention, no product URL.
    - No: neither, or a technical depth-only thread where a mention adds nothing.
    """
    if strong_hits:
        return "Yes"
    soft_set = {k.lower() for k in soft_mention_keywords}
    if {h.lower() for h in medium_hits} & soft_set:
        return "Soft"
    return "No"


def summarize(post: RedditPost, max_chars: int = 180) -> str:
    """One-sentence description for the sheet."""
    # Prefer a line from the body that doesn't start with markdown formatting
    body_lines = [ln.strip() for ln in post.selftext.split("\n") if ln.strip()]
    candidate = ""
    for line in body_lines:
        if line.startswith(("#", ">", "*", "-", "**TL;DR", "TL;DR")):
            continue
        if len(line) > 20:
            candidate = line
            break
    if not candidate:
        return post.title[:max_chars]
    return (candidate[:max_chars] + ("..." if len(candidate) > max_chars else "")).strip()


def score_post(
    post: RedditPost,
    strong: list[str],
    medium: list[str],
    pain: list[str],
    technical_terms: list[str],
    soft_mention_keywords: list[str] | None = None,
) -> ScoredPost:
    fit, sh, mh, ph = score_fit(post, strong, medium, pain)
    diff = classify_difficulty(post, technical_terms)
    mention_product = decide_mention_product(sh, mh, soft_mention_keywords or [])
    summary = summarize(post)
    return ScoredPost(
        post=post,
        fit_score=fit,
        difficulty=diff,
        mention_product=mention_product,
        summary=summary,
        strong_hits=sh,
        medium_hits=mh,
        pain_hits=ph,
    )


def passes_filters(scored: ScoredPost, min_score: float, window_days: int) -> bool:
    p = scored.post
    if p.stickied or p.over_18 or p.is_promoted:
        return False
    age_s = datetime.now(timezone.utc).timestamp() - p.created_utc
    if age_s > window_days * 86400:
        return False
    if scored.fit_score < min_score:
        return False
    # Must have strong OR (medium + pain)
    if not (scored.strong_hits or (scored.medium_hits and scored.pain_hits)):
        return False
    return True


def age_label(created_utc: float) -> str:
    age_h = (datetime.now(timezone.utc).timestamp() - created_utc) / 3600
    if age_h < 24:
        return f"{round(age_h, 1)}h"
    return f"{round(age_h / 24, 1)}d"
