"""One poll pass: pull matching open issues from the watchlist, queue the new
ones, and ping the phone. Designed to be cron'd (termux-job-scheduler or a
simple sleep loop) -- every call is idempotent thanks to `record_if_new`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .candidate_cache import CandidateCache
from .config import CANDIDATE_CACHE_TTL_HOURS, Settings, WatchedRepo
from .config import settings as default_settings
from .github_client import list_issues
from .notify import notify
from .paid_bounties import ScanReport, rank_opportunities, scan_funded_issues
from .state import IssueQueue


@dataclass(frozen=True)
class WatchResult:
    repo: str
    newly_queued: int
    checked: int


def _poll_repo(repo: WatchedRepo, queue: IssueQueue, notify_disabled: bool) -> WatchResult:
    # A repo's very first poll pass backfills its whole current open-issue
    # list -- that's normal history, not "news", so it must never fire one
    # notification per issue. Seed silently, then send a single summary ping.
    is_first_look = not queue.has_seen_repo(repo.owner_repo)
    issues = list_issues(repo.owner_repo, repo.labels)
    newly_queued = 0
    for issue in issues:
        url = issue.get("url", "")
        number = issue.get("number", 0)
        title = issue.get("title", "(untitled)")
        if not url or not queue.record_if_new(repo.owner_repo, number, title, url):
            continue
        newly_queued += 1
        if not is_first_look and not notify_disabled:
            notify(f"New bounty: {repo.owner_repo}#{number}", title, url=url)
        queue.mark_notified(url)
    if is_first_look and newly_queued and not notify_disabled:
        notify(
            f"Watching {repo.owner_repo}",
            f"Seeded {newly_queued} open issue(s) into the queue -- browse with `queue`.",
        )
    return WatchResult(repo=repo.owner_repo, newly_queued=newly_queued, checked=len(issues))


def run_watch_pass(config: Settings | None = None) -> list[WatchResult]:
    """Poll every watched repo once. Never raises -- a bad repo entry is
    isolated inside `list_issues`'s own fail-soft return of []."""
    config = config or default_settings
    queue = IssueQueue(config.db_path)
    return [_poll_repo(r, queue, config.notify_disabled) for r in config.watchlist]


def run_paid_bounty_pass(config: Settings | None = None) -> ScanReport:
    """One pass of the global paid-bounty search (see `paid_bounties.py`).

    Separate entry point from `run_watch_pass` on purpose -- different data
    source (global search vs. curated repo list), different cadence, different
    meaning (real money vs. give-back karma). Returns the full `ScanReport`
    (not just a count) so callers can see real coverage, not just a winner
    count -- "repeatedly scanning the same rejected issues" should show up
    as cache hits here, not as silent repeated work.
    """
    config = config or default_settings
    queue = IssueQueue(config.db_path)
    cache = CandidateCache(config.db_path, ttl_hours=CANDIDATE_CACHE_TTL_HOURS)
    report = scan_funded_issues(cache=cache)
    ranked = rank_opportunities(report.eligible)
    for issue in ranked:
        is_new = queue.record_if_new(
            issue.repo, issue.number, issue.title, issue.url,
            source="paid_bounty", bounty_label=issue.bounty_label,
        )
        if not is_new:
            continue
        if not config.notify_disabled:
            notify(f"\U0001f4b0 Paid bounty ({issue.bounty_label}): {issue.repo}", issue.title, url=issue.url)
        queue.mark_notified(issue.url)
    return report
