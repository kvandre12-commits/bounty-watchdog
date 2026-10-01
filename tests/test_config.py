from __future__ import annotations

import unittest

from bounty_watchdog.config import DEFAULT_WATCHLIST, Settings


class ConfigTests(unittest.TestCase):
    def test_default_watchlist_has_owner_slash_repo_entries(self) -> None:
        for repo in DEFAULT_WATCHLIST:
            self.assertIn("/", repo.owner_repo)
            self.assertTrue(repo.labels)

    def test_default_watchlist_has_no_duplicates(self) -> None:
        names = [r.owner_repo for r in DEFAULT_WATCHLIST]
        self.assertEqual(len(names), len(set(names)))

    def test_notify_disabled_reads_env(self) -> None:
        import os

        os.environ["BOUNTY_WATCHDOG_NO_NOTIFY"] = "true"
        try:
            self.assertTrue(Settings().notify_disabled)
        finally:
            del os.environ["BOUNTY_WATCHDOG_NO_NOTIFY"]

    def test_notify_enabled_by_default(self) -> None:
        import os

        os.environ.pop("BOUNTY_WATCHDOG_NO_NOTIFY", None)
        self.assertFalse(Settings().notify_disabled)


if __name__ == "__main__":
    unittest.main()
