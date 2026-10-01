from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bounty_watchdog.config import Settings, WatchedRepo
from bounty_watchdog.paid_bounties import CompetitionEvidence, FundedIssue, ScanReport

_NO_COMPETITION = CompetitionEvidence(0, 0, False, 0, (), 0, 0)
from bounty_watchdog.state import IssueQueue
from bounty_watchdog.watch import run_paid_bounty_pass, run_watch_pass


def _report(eligible: list[FundedIssue]) -> ScanReport:
    return ScanReport(
        eligible=eligible, total_fetched=len(eligible), evaluated=len(eligible),
        cache_hits=0, repo_cap_skips=0,
    )


def _funded(repo="o/r", number=1, amount=250.0) -> FundedIssue:
    return FundedIssue(
        repo=repo, number=number, title="fix x", url=f"https://x/{number}",
        amount_usd=amount, stars=100, is_fork=False, language="Python",
        comment_count=1, body_length=50, competition=_NO_COMPETITION,
    )


class RunWatchPassTests(unittest.TestCase):
    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.list_issues")
    def test_first_look_seeds_silently_with_one_summary_ping(self, list_issues, notify) -> None:
        list_issues.return_value = [
            {"number": 1, "title": "a", "url": "https://x/1"},
            {"number": 2, "title": "b", "url": "https://x/2"},
        ]
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db", watchlist=(WatchedRepo("o/r"),))
            results = run_watch_pass(cfg)
            pending = IssueQueue(cfg.db_path).pending()
        self.assertEqual(results[0].newly_queued, 2)
        self.assertEqual(len(pending), 2)
        # one summary notification, not one per issue
        self.assertEqual(notify.call_count, 1)
        self.assertIn("Seeded 2", notify.call_args.args[1])

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.list_issues")
    def test_second_pass_notifies_per_new_issue_not_summary(self, list_issues, notify) -> None:
        list_issues.return_value = [{"number": 1, "title": "a", "url": "https://x/1"}]
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db", watchlist=(WatchedRepo("o/r"),))
            run_watch_pass(cfg)  # first look, seeds silently (1 summary ping)
            notify.reset_mock()
            list_issues.return_value = [
                {"number": 1, "title": "a", "url": "https://x/1"},  # already seen
                {"number": 2, "title": "b", "url": "https://x/2"},  # genuinely new
            ]
            results = run_watch_pass(cfg)
        self.assertEqual(results[0].newly_queued, 1)
        notify.assert_called_once()
        self.assertIn("New bounty", notify.call_args.args[0])

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.list_issues")
    @patch.dict("os.environ", {"BOUNTY_WATCHDOG_NO_NOTIFY": "1"})
    def test_notify_disabled_flag_suppresses_notification(self, list_issues, notify) -> None:
        list_issues.return_value = [{"number": 1, "title": "a", "url": "https://x/1"}]
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db", watchlist=(WatchedRepo("o/r"),))
            results = run_watch_pass(cfg)
        self.assertEqual(results[0].newly_queued, 1)  # still queued
        notify.assert_not_called()  # but the phone stays quiet

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.list_issues", return_value=[])
    def test_repo_with_no_issues_is_a_noop(self, _list_issues, notify) -> None:
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db", watchlist=(WatchedRepo("o/r"),))
            results = run_watch_pass(cfg)
        self.assertEqual(results[0].newly_queued, 0)
        self.assertEqual(results[0].checked, 0)
        notify.assert_not_called()


class RunPaidBountyPassTests(unittest.TestCase):
    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.scan_funded_issues")
    def test_new_funded_issue_is_queued_and_notified(self, scan, notify) -> None:
        scan.return_value = _report([_funded(amount=250.0)])
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db")
            report = run_paid_bounty_pass(cfg)
            pending = IssueQueue(cfg.db_path).pending()
        self.assertEqual(len(report.eligible), 1)
        self.assertEqual(pending[0].source, "paid_bounty")
        self.assertEqual(pending[0].bounty_label, "$250")
        notify.assert_called_once()
        self.assertIn("$250", notify.call_args.args[0])

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.scan_funded_issues")
    def test_already_queued_issue_is_not_renotified(self, scan, notify) -> None:
        scan.return_value = _report([_funded()])
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db")
            run_paid_bounty_pass(cfg)
            notify.reset_mock()
            run_paid_bounty_pass(cfg)
        notify.assert_not_called()

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.scan_funded_issues")
    def test_no_funded_issues_is_a_noop(self, scan, notify) -> None:
        scan.return_value = _report([])
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db")
            report = run_paid_bounty_pass(cfg)
        self.assertEqual(report.eligible, [])
        notify.assert_not_called()

    @patch("bounty_watchdog.watch.notify")
    @patch("bounty_watchdog.watch.scan_funded_issues")
    def test_ranks_by_reward_before_queuing(self, scan, notify) -> None:
        scan.return_value = _report([_funded(number=1, amount=50.0), _funded(number=2, amount=500.0)])
        with TemporaryDirectory() as d:
            cfg = Settings(db_path=Path(d) / "q.db")
            run_paid_bounty_pass(cfg)
        # Highest-reward issue notified first.
        first_call_body = notify.call_args_list[0].args[0]
        self.assertIn("$500", first_call_body)


if __name__ == "__main__":
    unittest.main()
