# bounty-watchdog

Termux-native "give back" automation: monitors watched GitHub repos for
fresh, fixable-looking issues, pings your phone the second one shows up,
and (once you or an agent has drafted+tested a fix) opens the PR **as a
draft** so nothing ever ships without a human tapping "ready".

This is the boring-parts automation for the ritual: *find a real issue,
fix it well, test it, PR it, never spam maintainers.* It does **not**
auto-write fixes — that's still you or an interactively-invoked coding
agent. This bot just makes sure you never miss a good one, and never
accidentally auto-submit a bad one.

## Opportunity funnel

Always evaluate opportunities in this order:

```text
fresh technical pain
→ detect willingness-to-pay
→ identify neglected niches
→ verify payment
→ audit
→ build
```

A fixable issue is not automatically an opportunity. Do not begin with stale
bounty catalogs, infer payment from an old badge, or write code before the
commercial and contribution gates pass. Implementation is deliberately last.

## Why draft-PR-first, not "auto-submit"

Same design rule as `SharpEdge-Robinhood-Bridge`: automation may **tighten**
(gate, test, hold in draft) but never **loosen** (auto-submit, auto-merge).
Every PR this bot opens starts as a draft. A human runs
`bounty_watchdog ready <pr-url>` (or taps the notification action) to flip
it live. Nothing goes out the door untouched.

## Pieces

- `config.py` — the watchlist (repo + labels), single source of truth.
- `github_client.py` — thin `gh` CLI wrapper (list issues, open draft PR,
  mark ready). Fails soft: a broken repo/network never crashes the bot.
- `state.py` — tiny sqlite queue (`new` -> `notified` -> `claimed` -> `pr_open`).
- `notify.py` — `termux-notification` wrapper with a tap-to-open action.
  Falls back to stdout off-Android (e.g. CI) so it's still testable.
- `watch.py` — one poll pass: find new issues, queue + notify them.
- `submit.py` — run tests locally; only on green does it push + open a
  **draft** PR and notify for review.
- `cli.py` — `watch` / `queue` / `claim` / `submit` / `ready` subcommands.

## Usage

```bash
# one poll pass across the configured watchlist (cron this, e.g. every 15m)
python -m bounty_watchdog watch

# see what's queued
python -m bounty_watchdog queue

# claim one to work on (prints a ready-to-go briefing)
python -m bounty_watchdog claim <issue-url>

# after you/the agent have a tested fix on a branch in the target repo clone:
python -m bounty_watchdog submit /path/to/clone fix-branch-name \
    --title "fix: ..." --body "Fixes #123" --test-cmd "pytest -q"

# tap the phone notification, or manually:
python -m bounty_watchdog ready <pr-url>
```

## Cron it (Termux)

```bash
termux-job-scheduler --script $(pwd)/scripts/poll.sh --period-ms 900000
```

## Dev test

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py'
```
