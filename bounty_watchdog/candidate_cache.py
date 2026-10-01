"""Evaluation memoization: don't re-spend API calls re-checking the same
candidate issue every single run.

"Repeatedly scanning the same rejected issues is not new coverage." This is
a separate concern from `state.IssueQueue` on purpose -- the queue is the
user-facing new/notified/claimed/pr_open lifecycle; this is a purely
internal bookkeeping cache of "what have we already looked at and why,"
including candidates that never made it into the queue at all because they
were rejected. Same sqlite file, different table -- no reason to manage two
database files for one small tool.

Versioned on purpose: a TTL alone only answers "has enough TIME passed to
re-check this." It says nothing about whether the EVALUATION LOGIC ITSELF
changed in the meantime. Verified live -- after replacing a blunt
competition threshold with real merge-status checking, a subsequent scan
still reported the OLD "high_competition" rejection reason for cached
candidates, because the cache had no way to know the code that produced
that verdict was gone. Every cached row now carries the logic version that
produced it; a version mismatch is treated as stale regardless of TTL.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_cache (
    url TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    detail TEXT,
    checked_at TEXT NOT NULL
);
"""

# Added after the original schema shipped -- idempotent, checked against
# pragma table_info first, same migration pattern as state.py. Old rows
# default to version 0, which will never match a real (>=1) current
# version, so every pre-versioning cache entry is correctly treated as
# stale exactly once, then recorded fresh with a real version going forward.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("logic_version", "INTEGER NOT NULL DEFAULT 0"),
)


@dataclass(frozen=True)
class CachedOutcome:
    url: str
    outcome: str
    detail: str | None
    checked_at: str
    logic_version: int


class CandidateCache:
    def __init__(self, db_path: Path, *, ttl_hours: float):
        self.db_path = db_path
        self.ttl = timedelta(hours=ttl_hours)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            self._apply_migrations(conn)

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(candidate_cache)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE candidate_cache ADD COLUMN {column} {ddl}")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def get_fresh(self, url: str, *, logic_version: int) -> CachedOutcome | None:
        """Return the cached outcome iff it's within the TTL window AND was
        produced by the CURRENT evaluation logic version. Either a TTL
        expiry or a version mismatch means the caller re-evaluates --
        correctness over cache-hit rate.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_cache WHERE url=?", (url,)
            ).fetchone()
        if row is None:
            return None
        if row["logic_version"] != logic_version:
            return None
        checked_at = datetime.fromisoformat(row["checked_at"])
        if datetime.now(UTC) - checked_at > self.ttl:
            return None
        return CachedOutcome(
            url=row["url"],
            outcome=row["outcome"],
            detail=row["detail"],
            checked_at=row["checked_at"],
            logic_version=row["logic_version"],
        )

    def record(self, url: str, outcome: str, *, logic_version: int, detail: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO candidate_cache (url, outcome, detail, checked_at, logic_version) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET outcome=excluded.outcome, "
                "detail=excluded.detail, checked_at=excluded.checked_at, "
                "logic_version=excluded.logic_version",
                (url, outcome, detail, datetime.now(UTC).isoformat(), logic_version),
            )
