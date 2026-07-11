# AGENTS.md

Instructions for AI coding agents working in this repo. Human contributors:
see [CONTRIBUTING.md](CONTRIBUTING.md). This file follows the
[AGENTS.md](https://agents.md) convention.

## What this project is

A configurable Reddit social-listening template. On an hourly cron it sweeps
subreddits, scores posts by buyer intent, classifies difficulty, optionally
drafts a reply with Claude, appends a row to a Google Sheet, and optionally
fires a Slack alert. It is a template: everything product-specific is meant to
live in `config.yml`, not in code.

## Setup and common commands

```bash
./setup.sh                 # venv + deps + .env + a dry run (one command)

# Or manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dry run: exercises sweep + scoring + row-building, writes nowhere, spends nothing
python listener.py --dry-run --no-draft --no-profile --limit 5

python listener.py         # full run (needs .env configured)
python scripts/smoke_test.py   # verify the Apps Script sheet-writer endpoint
```

There is no unit-test suite yet. The dry run above is the smoke test for
listener changes.

## Architecture (where things live)

- `listener.py` - entry point; loads `config.yml` + `.env`, orchestrates the run.
- `src/reddit_client.py` - Reddit JSON API sweep (optionally via Browserless).
- `src/scorer.py` - fit score, Easy/Medium/Technical classifier, Mention Product? logic.
- `src/drafter.py` - Claude drafting. Voice comes from `config.yml` `voice_rules`; `DEFAULT_VOICE_RULES` is only a fallback.
- `src/browserless_client.py` - optional OP profile lookup + docs grounding.
- `src/slack_notifier.py` - optional high-score alerts.
- `src/sheet_writer.py` - POST rows to the Apps Script endpoint.
- `src/db.py` - SQLite seen-list cache.

## Rules for changes

- **Config, not code.** Anything specific to one product or niche (keywords,
  persona, product names, URLs, voice) belongs in `config.yml`. If a change
  hardcodes any of those in Python, it belongs in config instead.
- **Optional integrations stay optional.** The listener must run end to end
  with only `SHEET_WRITER_URL` and `SHEET_WRITER_TOKEN` set. Browserless,
  Slack, and docs grounding no-op when their env vars are missing; new
  integrations follow the same pattern.
- **Never commit secrets.** Credentials go in `.env` (gitignored) and CI
  secrets. New credential? Add a placeholder to `.env.example` and document it
  in `DEPLOY.md` and the README "Make it yours" table.
- **Python 3.11+, standard library where practical.** Keep the dependency list
  small (currently five packages).

## Style

- Match surrounding code: plain functions, type hints where they help,
  docstrings on modules and public functions.
- Log to stdout with the existing bracketed-timestamp format; no logging
  framework.
- Follow the drafter's own voice rules in any generated Reddit copy: first
  person, no em dashes, no "it's not X, it's Y", no filler vocabulary.

## Validating a change

Before proposing a diff, run the dry run above and confirm it still completes.
If you touched the sheet writer, run `scripts/smoke_test.py` against a test
sheet. Note that Reddit rate-limits datacenter/CI IPs, so a dry run may fetch
0 posts from a cloud box; that is environmental, not a regression.
