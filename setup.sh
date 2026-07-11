#!/usr/bin/env bash
# One-command setup for the Reddit Social Listener.
#
# Creates a virtualenv, installs dependencies, seeds .env from the example,
# and runs a dry sweep so you can confirm the pipeline works before touching
# any config. Safe to re-run: it won't overwrite an existing .env.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Creating virtualenv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  echo "==> Seeding .env from .env.example (fill in your credentials next)"
  cp .env.example .env
else
  echo "==> .env already exists, leaving it untouched"
fi

echo "==> Dry run (no sheet writes, no drafting, no paid API calls)"
python listener.py --dry-run --no-draft --no-profile --limit 5 || true

cat <<'EOF'

Setup complete. Next steps:
  1. Edit config.yml   - persona, subs, keywords, voice (see "Make it yours" in the README)
  2. Fill in .env      - ANTHROPIC_API_KEY, SHEET_WRITER_URL, SHEET_WRITER_TOKEN (see DEPLOY.md)
  3. Re-run:           source .venv/bin/activate && python listener.py --dry-run --limit 5

Note: a dry run from a datacenter/CI IP may fetch 0 posts (Reddit rate-limits
those). Run from a residential connection or set USE_BROWSERLESS_FETCH=1.
EOF
