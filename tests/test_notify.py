from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bounty_watchdog.notify import notify


class NotifyTests(unittest.TestCase):
    @patch("bounty_watchdog.notify.shutil.which", return_value=None)
    def test_falls_back_to_print_off_android(self, _which) -> None:
        self.assertFalse(notify("t", "c"))

    @patch("bounty_watchdog.notify.subprocess.run")
    @patch("bounty_watchdog.notify.shutil.which", return_value="/usr/bin/termux-notification")
    def test_returns_true_on_success(self, _which, run) -> None:
        run.return_value = MagicMock(returncode=0)
        self.assertTrue(notify("t", "c", url="https://x"))

    @patch("bounty_watchdog.notify.subprocess.run", side_effect=OSError("nope"))
    @patch("bounty_watchdog.notify.shutil.which", return_value="/usr/bin/termux-notification")
    def test_fails_soft_on_subprocess_error(self, _which, _run) -> None:
        self.assertFalse(notify("t", "c"))


if __name__ == "__main__":
    unittest.main()
