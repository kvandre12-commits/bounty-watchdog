from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bounty_watchdog.submit import submit_fix


class SubmitFixTests(unittest.TestCase):
    @patch("bounty_watchdog.submit.notify")
    @patch("bounty_watchdog.submit.create_draft_pr")
    @patch("bounty_watchdog.submit.subprocess.run")
    def test_tests_pass_opens_draft_pr(self, run, create_pr, notify) -> None:
        run.return_value = MagicMock(returncode=0, stdout="2 passed", stderr="")
        create_pr.return_value = "https://github.com/o/r/pull/9"
        result = submit_fix(Path("."), title="fix: x", body="Fixes #1")
        self.assertEqual(result.status, "draft_opened")
        self.assertEqual(result.pr_url, "https://github.com/o/r/pull/9")
        notify.assert_called_once()

    @patch("bounty_watchdog.submit.notify")
    @patch("bounty_watchdog.submit.create_draft_pr")
    @patch("bounty_watchdog.submit.subprocess.run")
    def test_tests_fail_never_opens_pr(self, run, create_pr, notify) -> None:
        run.return_value = MagicMock(returncode=1, stdout="1 failed", stderr="")
        result = submit_fix(Path("."), title="fix: x", body="Fixes #1")
        self.assertEqual(result.status, "tests_failed")
        create_pr.assert_not_called()
        notify.assert_called_once()

    @patch("bounty_watchdog.submit.notify")
    @patch("bounty_watchdog.submit.create_draft_pr", return_value=None)
    @patch("bounty_watchdog.submit.subprocess.run")
    def test_pr_creation_failure_is_reported(self, run, _create_pr, notify) -> None:
        run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = submit_fix(Path("."), title="fix: x", body="Fixes #1")
        self.assertEqual(result.status, "pr_failed")
        self.assertIsNone(result.pr_url)
        notify.assert_called_once()

    @patch("bounty_watchdog.submit.notify")
    @patch("bounty_watchdog.submit.subprocess.run", side_effect=OSError("no such command"))
    def test_broken_test_command_fails_soft(self, _run, notify) -> None:
        result = submit_fix(Path("."), title="t", body="b", test_command="not-a-real-cmd")
        self.assertEqual(result.status, "tests_failed")
        self.assertIn("could not run", result.detail)
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
