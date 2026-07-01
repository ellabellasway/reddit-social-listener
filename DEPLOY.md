# Deployment walkthrough

Step-by-step from "code on disk" to "hourly cron writing to the master sheet."

Estimated time: 30-45 minutes. Most of it is Apps Script / Google permissions clicking.

Before starting, edit `config.yml`: fill in the `persona` block and replace
the example `subs`/keyword lists with your own product's vocabulary. The
defaults are illustrative examples for a browser-automation product and
won't score anything meaningfully for a different niche.

## 0. What you'll end up with

- A master Google Sheet you own
- An Apps Script web app deployed under that sheet, receiving POSTs from the listener
- A GitHub repo running the listener on an hourly cron

## 1. Create the master sheet

1. Google Drive > New > Google Sheets
2. Rename to something like **"Reddit Triage - Master"**
3. Share > add anyone who'll be picking up threads as Editors

Copy the sheet's URL. The ID is the long string between `/d/` and `/edit`.
Paste the full URL into `config.yml`'s `sheet_url` (used for Slack alert links).

## 2. Add the Apps Script web app

In the master sheet:

1. Extensions > Apps Script
2. Delete the default `Code.gs` contents
3. Open `apps_script/SheetWriter.gs` in this repo, copy the whole file, paste into `Code.gs`
4. Generate a long random token:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   Save this string somewhere safe - you'll need it twice.
5. Replace the `SHARED_TOKEN` constant at the top of `Code.gs` with that string
6. Save (cmd+S). Name the project "Reddit Listener Sheet Writer."
7. In the Apps Script editor, run the `setupSheet` function once:
   - Select `setupSheet` from the function dropdown
   - Click Run
   - Authorize when prompted (it'll warn about an unverified app; that's fine, it's your own script). Click Advanced > Go to project (unsafe). Accept the scopes.
   - On success, switch back to the sheet tab. You should see a "Triage" tab with 15 columns, frozen header, and dropdown validation on Status/Difficulty/Mention Product?.

## 3. Deploy as web app

In the Apps Script editor:

1. Deploy > New deployment
2. Type (gear icon) > Web app
3. Configure:
   - **Description**: "Reddit listener sheet writer v1"
   - **Execute as**: Me (your account - the sheet owner)
   - **Who has access**: Anyone with the link
4. Click Deploy
5. Authorize again if prompted
6. Copy the **Web app URL** (looks like `https://script.google.com/macros/s/AKfyc.../exec`)

Save the token from step 2 and this URL into `.env` (see step 4), then test with:

```bash
python scripts/smoke_test.py "$SHEET_WRITER_URL"
```

If the test row shows up at the bottom of the Triage tab, you're good. Delete the test row.

## 4. Configure credentials

```bash
cp .env.example .env
```

Fill in:
- `ANTHROPIC_API_KEY` - create at https://console.anthropic.com. Set a monthly usage cap (e.g. $20/mo) - the listener uses ~$0.05 per draft.
- `SHEET_WRITER_URL` - from step 3.6
- `SHEET_WRITER_TOKEN` - the same random string used in `SHARED_TOKEN` in step 2.4
- `BROWSERLESS_API_KEY` - optional, only needed for OP profile filtering / docs grounding / the CI Reddit-fetch workaround. Get one at https://browserless.io if you want those features.
- `SLACK_WEBHOOK_URL` - optional, for high-score thread alerts
- `DOCS_SITEMAP_URL` - optional, your own docs site's sitemap for grounding Technical replies

## 5. Install + dry run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Dry-run sweep (no sheet write, prints sample row)
python listener.py --dry-run --no-draft --limit 5
```

## 6. Push to GitHub and add secrets

```bash
gh repo create your-username/reddit-social-listener --private --source=. --push
```

Settings > Secrets and variables > Actions > New repository secret. Add each of:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | From step 4 |
| `SHEET_WRITER_URL` | From step 3.6 |
| `SHEET_WRITER_TOKEN` | Same random string as `SHARED_TOKEN` |
| `BROWSERLESS_API_KEY` | Optional, from step 4 |
| `SLACK_WEBHOOK_URL` | Optional, from step 4 |

## 7. Seed one example row (optional sanity check)

```bash
source .venv/bin/activate
python scripts/seed_example_rows.py
```

Check the sheet - you should see one row with Status=New. Edit `scripts/seed_example_rows.py`'s `ROWS` or delete the script once you trust the pipeline.

## 8. Trigger the first cron run

In GitHub: Actions > Reddit Listener > Run workflow > Run.

The first run takes a few minutes (downloads deps + sweeps your configured subs + drafts each new candidate). Subsequent runs are faster since the seen-list and docs cache are cached between runs.

Verify rows appear in the sheet. If something's wrong, check the workflow logs.

## 9. Going forward

- The cron runs hourly. New high-fit threads will appear in the sheet automatically.
- Claim threads by setting `Owner` and updating `Status` from "New" to "Drafting".
- After posting, paste the Reddit comment URL into `Posted URL` and flip Status to "Posted".

## Troubleshooting

**Empty draft column on new rows.** Anthropic API key isn't set or has run out of credit. Check secret value + console.anthropic.com usage.

**`HTTP 500` from Apps Script.** Open the script editor > Executions tab to see the error. Most likely cause: `SHARED_TOKEN` mismatch between Apps Script and `.env`.

**No rows surfacing.** Sweep may be hitting the seen-list cache. Delete the cache in GitHub Actions (Settings > Actions > Caches > delete `listener-state-v2-*`).

**Listener finds spam threads.** Tighten the OP filters in `config.yml`:
```yaml
op_filters:
  min_account_age_days: 90    # was 30
  min_karma: 500              # was 100
```

**Listener misses threads.** Lower the score threshold:
```yaml
min_score: 15    # was 30
```

Or add the missing sub to the `subs:` list.

## Rollback

If you need to stop the cron, just disable the workflow:

GitHub > Actions > Reddit Listener > "..." menu > Disable workflow.

The sheet keeps everything - no data is lost.
