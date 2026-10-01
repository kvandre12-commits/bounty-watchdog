"""Human-friendly CLI: watch / queue / claim / submit / ready.

Usage:
    python -m bounty_watchdog watch
    python -m bounty_watchdog queue
    python -m bounty_watchdog claim <issue-url>
    python -m bounty_watchdog submit <repo-path> --title T --body B [--test-cmd CMD]
    python -m bounty_watchdog ready <pr-url>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import settings
from .github_client import mark_pr_ready
from .state import IssueQueue
from .submit import submit_fix
from .watch import run_paid_bounty_pass, run_watch_pass


def _cmd_watch(_args: argparse.Namespace) -> int:
    results = run_watch_pass()
    total_new = sum(r.newly_queued for r in results)
    for r in results:
        print(f"  {r.repo}: checked {r.checked}, queued {r.newly_queued} new")
    print(f"\n{total_new} new issue(s) queued across {len(results)} repos.")
    return 0


def _cmd_paid(_args: argparse.Namespace) -> int:
    report = run_paid_bounty_pass()
    print(f"fetched {report.total_fetched}, evaluated {report.evaluated}, "
          f"cache hits {report.cache_hits}, repo-cap skips {report.repo_cap_skips}")
    print(f"eligible: {len(report.eligible)}")
    for reason, count in report.rejection_reasons.most_common():
        print(f"  rejected ({reason}): {count}")
    for repo, number, amount, title in (
        (m["repo"], m["number"], m["amount"], m["title"]) for m in report.near_misses
    ):
        print(f"  near-miss: {repo}#{number} ${amount} -- {title[:50]}")
    return 0


def _cmd_queue(_args: argparse.Namespace) -> int:
    queue = IssueQueue(settings.db_path)
    pending = queue.pending()
    if not pending:
        print("Queue is empty. Go touch grass.")
        return 0
    for issue in pending:
        tag = f" \U0001f4b0{issue.bounty_label}" if issue.bounty_label else ""
        print(
            f"[{issue.status:>8}][{issue.source}]{tag} {issue.repo}#{issue.number}  "
            f"{issue.title}\n  {issue.url}"
        )
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    queue = IssueQueue(settings.db_path)
    issue = queue.get(args.url)
    if issue is None:
        print(f"Not in queue: {args.url}", file=sys.stderr)
        return 1
    queue.mark_claimed(issue.url)
    print(f"Claimed {issue.repo}#{issue.number}: {issue.title}")
    print(f"  {issue.url}")
    print("\nSuggested next steps:")
    print(f"  gh repo clone {issue.repo} /tmp/{issue.repo.split('/')[-1]}")
    print(f"  cd /tmp/{issue.repo.split('/')[-1]} && git checkout -b fix-issue-{issue.number}")
    print("  # draft + test the fix, then:")
    print(
        f"  python -m bounty_watchdog submit /tmp/{issue.repo.split('/')[-1]} "
        f"--title 'fix: ...' --body 'Fixes #{issue.number}'"
    )
    return 0


def _cmd_submit(args: argparse.Namespace) -> int:
    result = submit_fix(
        Path(args.repo_path),
        title=args.title,
        body=args.body,
        base=args.base,
        test_command=args.test_cmd,
    )
    print(f"status: {result.status}")
    print(result.detail)
    if result.pr_url:
        print(f"draft PR: {result.pr_url}")
    return 0 if result.status == "draft_opened" else 1


def _cmd_ready(args: argparse.Namespace) -> int:
    ok = mark_pr_ready(args.pr_url)
    print("marked ready for review" if ok else "failed to mark ready")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="bounty-watchdog")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="poll the watchlist once").set_defaults(func=_cmd_watch)
    sub.add_parser(
        "paid", help="search for legit paid bounties (global, filtered)"
    ).set_defaults(func=_cmd_paid)
    sub.add_parser("queue", help="list pending queued issues").set_defaults(func=_cmd_queue)

    p_claim = sub.add_parser("claim", help="claim an issue and print a briefing")
    p_claim.add_argument("url")
    p_claim.set_defaults(func=_cmd_claim)

    p_submit = sub.add_parser("submit", help="run tests, open a draft PR on green")
    p_submit.add_argument("repo_path")
    p_submit.add_argument("--title", required=True)
    p_submit.add_argument("--body", required=True)
    p_submit.add_argument("--base", default="main")
    p_submit.add_argument("--test-cmd", default="pytest -q")
    p_submit.set_defaults(func=_cmd_submit)

    p_ready = sub.add_parser("ready", help="flip a draft PR to ready-for-review")
    p_ready.add_argument("pr_url")
    p_ready.set_defaults(func=_cmd_ready)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
