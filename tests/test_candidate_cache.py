from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from bounty_watchdog.candidate_cache import CandidateCache


class CandidateCacheTests(unittest.TestCase):
    def _cache(self, tmp: str, ttl_hours: float = 24) -> CandidateCache:
        return CandidateCache(Path(tmp) / "cache.db", ttl_hours=ttl_hours)

    def test_fresh_entry_is_returned(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d)
            c.record("https://x/1", "already_awarded", logic_version=2)
            cached = c.get_fresh("https://x/1", logic_version=2)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.outcome, "already_awarded")

    def test_missing_entry_returns_none(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d)
            self.assertIsNone(c.get_fresh("https://x/nope", logic_version=2))

    def test_stale_entry_returns_none(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d, ttl_hours=1)
            c.record("https://x/1", "is_fork", logic_version=2)
            # Manually backdate the row past the TTL.
            with c._connect() as conn:
                stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
                conn.execute("UPDATE candidate_cache SET checked_at=? WHERE url=?", (stale, "https://x/1"))
            self.assertIsNone(c.get_fresh("https://x/1", logic_version=2))

    def test_record_overwrites_previous_outcome(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d)
            c.record("https://x/1", "maintainer_inactive", logic_version=2)
            c.record("https://x/1", "eligible", logic_version=2, detail="$250")
            cached = c.get_fresh("https://x/1", logic_version=2)
        self.assertEqual(cached.outcome, "eligible")
        self.assertEqual(cached.detail, "$250")

    def test_version_mismatch_is_treated_as_stale(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d)
            c.record("https://x/1", "high_competition", logic_version=1)
            # Same URL, same TTL window, but the evaluation logic has since
            # changed -- this must NOT be trusted even though it's fresh by
            # time alone.
            self.assertIsNone(c.get_fresh("https://x/1", logic_version=2))

    def test_old_unversioned_rows_default_to_version_zero_and_are_stale(self) -> None:
        with TemporaryDirectory() as d:
            c = self._cache(d)
            with c._connect() as conn:
                conn.execute(
                    "INSERT INTO candidate_cache (url, outcome, detail, checked_at) "
                    "VALUES (?, ?, ?, ?)",
                    ("https://x/1", "is_fork", None, datetime.now(UTC).isoformat()),
                )
            self.assertIsNone(c.get_fresh("https://x/1", logic_version=1))


if __name__ == "__main__":
    unittest.main()
