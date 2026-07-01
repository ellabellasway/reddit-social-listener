# Reddit Social Listener

Surfaces relevant Reddit threads into a shared master Google Sheet where you
(or your team) can pick up a thread and draft a reply. Runs on an hourly cron
and optionally uses [Browserless](https://browserless.io) for reliable
cloud-IP fetching and live docs grounding - swap that piece out if you'd
rather use something else.

This started as an internal tool for surfacing Reddit threads relevant to a
browser-automation product; this repo is the genericized template version.
Everything product-specific lives in `config.yml` - copy it, fill in your
own persona and keywords, and it's yours.

## What it does

Hourly cron job that:

1. Sweeps target subs via Reddit's public JSON API
2. Scores posts by buyer-intent keywords + engagement
3. Classifies difficulty (Easy / Medium / Technical) so a team can split up who answers what
4. Optionally renders the OP's profile page (account age + karma) to filter throwaway accounts
5. For Technical threads, optionally searches your own docs site for grounding context
6. Drafts a starter reply via Claude using your configured voice/persona
7. Appends a row to the master Google Sheet

You (or your team) pick rows out of the sheet, edit the draft, post the
reply, and fill in the Posted URL column.

## Project structure

```
reddit-social-listener/
├── README.md                  # this file
├── DEPLOY.md                  # step-by-step deployment walkthrough
├── config.yml                 # persona, sub list, keywords, scoring thresholds - START HERE
├── listener.py                # main cron entry
├── requirements.txt
├── .env.example
├── apps_script/
│   └── SheetWriter.gs         # Apps Script web app for sheet writes
├── src/
│   ├── reddit_client.py       # Reddit JSON API wrapper
│   ├── browserless_client.py  # optional: OP profile lookup + docs search via Browserless
│   ├── scorer.py              # fit + difficulty + Mention Product? classification
│   ├── drafter.py             # Claude API drafting with your configured voice rules
│   ├── sheet_writer.py        # POST rows to Apps Script
│   └── db.py                  # SQLite seen-list cache
└── scripts/
    ├── smoke_test.py          # one-off: verify the sheet-writer endpoint works
    └── seed_example_rows.py   # one-off: write a placeholder row to confirm the pipeline
```

## Quick start

For step-by-step deployment, see **[DEPLOY.md](DEPLOY.md)**. The rest of this README is reference material.

1. Edit `config.yml`: fill in `persona`, replace the example `subs`/keyword lists with your own.
2. `cp .env.example .env` and fill in credentials (see DEPLOY.md).
3. `pip install -r requirements.txt`
4. `python listener.py --dry-run --no-draft --limit 5` to sanity-check without writing anywhere.
5. Deploy `apps_script/SheetWriter.gs`, wire up the GitHub Actions cron (already in `.github/workflows/listener.yml`).

## How the difficulty classifier works

For each post:

| Signal | Bucket impact |
|---|---|
| 1+ code block in body, OR stack trace pattern | Technical |
| 5+ technical-term hits (config.yml `technical_terms`) | Technical |
| 2-4 technical-term hits | Medium |
| 0-1 technical hits + question-word title + short body | Easy |
| 0-1 technical hits + body < 1500 chars | Easy |
| Otherwise | Medium |

Tunable via `config.yml`'s `technical_terms` list.

## Browserless's role (optional)

Two concrete, optional uses:

1. **OP profile lookup.** Reddit JSON doesn't reliably include account age or karma. `src/browserless_client.py` can render `old.reddit.com/user/X` and parse it. Cached 7 days per user. Disable with `--no-profile`.
2. **Docs grounding for Technical threads.** If you set `DOCS_SITEMAP_URL` in `.env`, the listener renders relevant pages from your own docs site before drafting a technical reply, so the model isn't guessing at feature names.
3. Reddit fetches from CI (GitHub Actions IPs get rate-limited by Reddit's anonymous JSON) can route through Browserless via `USE_BROWSERLESS_FETCH=1` - see `.github/workflows/listener.yml`.

None of this is required. Without `BROWSERLESS_API_KEY` set, OP filtering and docs grounding just no-op and the listener still runs.

## Voice rules (encoded in drafter.py, driven by config.yml)

- First person, conversational
- 2-4 paragraphs separated by blank lines (Reddit needs this for paragraph breaks)
- No em dashes, en dashes, arrows
- No "isn't X, it's Y" patterns (AI tell)
- No filler vocabulary (leverage, seamless, robust, delve, etc.)
- Mention your product only when "Mention Product?" = Yes (literal product) or Soft (credibility-only)
- Reference specific commenters by username when useful

## Known limitations

- Anonymous Reddit JSON rate-limits at ~60 req/min; keep your sweep well under that
- Anthropic API calls cost real money; estimate ~$0.05 per drafted reply with Opus-class models
- The keyword lists in `config.yml` are illustrative examples for a browser-automation product - they won't score anything meaningfully for a different niche until you replace them

## Next steps / ideas

- [ ] Owner/team-member assignment automation (round-robin or fit-based)
- [ ] Weekly digest mode (top N of the week, conversion rate from surfaced to posted)
- [ ] Extend to HN + X + dev.to using the same pattern
