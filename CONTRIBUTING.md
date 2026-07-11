# Contributing

Thanks for your interest. This is a small template project, so contributing is deliberately lightweight.

## Ways to contribute

- **Bug reports.** Open an issue with what you ran, what you expected, and what happened. Include the listener's log output if you have it (it prints everything to stdout). Strip any tokens or sheet URLs before pasting.
- **Fixes and small improvements.** Fork, branch, open a pull request. One change per PR keeps review fast.
- **Bigger features.** Open an issue first so we can talk about scope before you write code. The "Next steps / ideas" list at the bottom of the README is a good place to start.

## Ground rules for changes

- Keep the template generic. Anything specific to one product or niche belongs in `config.yml`, not in the Python code. If your change hardcodes a keyword list, a product name, or a URL, it probably belongs in config instead.
- Optional integrations must stay optional. The listener runs end to end with only `SHEET_WRITER_URL` and `SHEET_WRITER_TOKEN` set. Browserless, Slack, and docs grounding all no-op when their env vars are missing, and new integrations should follow the same pattern.
- No secrets in the repo, ever. Credentials go in `.env` (gitignored) locally and GitHub Actions secrets in CI. If you add a new credential, add a placeholder line to `.env.example` and document it in DEPLOY.md.
- Python 3.11+, standard library where practical. The dependency list is five packages and it would be nice to keep it around there.

## Testing your change

There's no test suite yet (see the ideas list). Before opening a PR:

```bash
pip install -r requirements.txt
python listener.py --dry-run --no-draft --no-profile --limit 5
```

A dry run exercises the sweep, scoring, and row-building path without writing anywhere or spending API credits. If your change touches the sheet writer, `scripts/smoke_test.py` verifies the Apps Script endpoint end to end against your own test sheet.

Note that Reddit's anonymous JSON API rate-limits many cloud and datacenter IPs, so a dry run from CI or a cloud box may fetch 0 posts. Run it from a residential connection or set `USE_BROWSERLESS_FETCH=1` if you hit this.

## Style

- Match the surrounding code. Plain functions, type hints where they help, docstrings on modules and public functions.
- Log to stdout with the existing bracketed-timestamp format rather than adding a logging framework.

## License

By contributing you agree your contributions are licensed under the MIT License that covers the project.
