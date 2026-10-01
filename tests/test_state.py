from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bounty_watchdog.state import IssueQueue


class IssueQueueTests(unittest.TestCase):
    def _queue(self, tmp: str) -> IssueQueue:
        return IssueQueue(Path(tmp) / "queue.db")

    def test_record_if_new_returns_true_once(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            first = q.record_if_new("o/r", 1, "title", "https://x/1")
            second = q.record_if_new("o/r", 1, "title", "https://x/1")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_pending_only_lists_new_and_notified(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            q.record_if_new("o/r", 1, "a", "https://x/1")
            q.record_if_new("o/r", 2, "b", "https://x/2")
            q.mark_claimed("https://x/2")
            pending = q.pending()
        self.assertEqual([i.url for i in pending], ["https://x/1"])

    def test_mark_notified_updates_status(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            q.record_if_new("o/r", 1, "a", "https://x/1")
            q.mark_notified("https://x/1")
            issue = q.get("https://x/1")
        self.assertEqual(issue.status, "notified")
        self.assertIsNotNone(issue.notified_at)

    def test_mark_pr_open_sets_pr_url(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            q.record_if_new("o/r", 1, "a", "https://x/1")
            q.mark_pr_open("https://x/1", "https://x/pr/9")
            issue = q.get("https://x/1")
        self.assertEqual(issue.status, "pr_open")
        self.assertEqual(issue.pr_url, "https://x/pr/9")

    def test_get_missing_returns_none(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            self.assertIsNone(q.get("https://nope"))

    def test_has_seen_repo_false_until_first_record(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            self.assertFalse(q.has_seen_repo("o/r"))
            q.record_if_new("o/r", 1, "a", "https://x/1")
            self.assertTrue(q.has_seen_repo("o/r"))
            self.assertFalse(q.has_seen_repo("other/repo"))

    def test_default_source_is_watchlist_with_no_bounty_label(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            q.record_if_new("o/r", 1, "a", "https://x/1")
            issue = q.get("https://x/1")
        self.assertEqual(issue.source, "watchlist")
        self.assertIsNone(issue.bounty_label)

    def test_paid_bounty_source_and_label_are_stored(self) -> None:
        with TemporaryDirectory() as d:
            q = self._queue(d)
            q.record_if_new(
                "o/r", 1, "a", "https://x/1", source="paid_bounty", bounty_label="$250"
            )
            issue = q.get("https://x/1")
        self.assertEqual(issue.source, "paid_bounty")
        self.assertEqual(issue.bounty_label, "$250")


if __name__ == "__main__":
    unittest.main()
