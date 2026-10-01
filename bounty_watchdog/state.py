"""Tiny sqlite-backed issue queue. One table, four states, no ORM overhead.

Status lifecycle: new -> notified -> claimed -> pr_open. Never goes backwards,
never auto-advances past `pr_open` -- that step is a human tapping "ready".
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'new',
    first_seen TEXT NOT NULL,
    notified_at TEXT,
    pr_url TEXT
);
"""

# Columns added after the original schema shipped. Applied via idempotent
# ALTER TABLE (checked against pragma table_info first) so the existing
# live queue.db never needs a destructive rebuild -- Zen of Python: "Errors
# should never pass silently" cuts both ways, a migration that explodes on
# a column that already exists is just as wrong as one that silently no-ops.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("source", "TEXT NOT NULL DEFAULT 'watchlist'"),
    ("bounty_label", "TEXT"),
)


@dataclass(frozen=True)
class QueuedIssue:
    id: int
    repo: str
    number: int
    title: str
    url: str
    status: str
    first_seen: str
    notified_at: str | None
    pr_url: str | None
    source: str
    bounty_label: str | None


def _row_to_issue(row: sqlite3.Row) -> QueuedIssue:
    # sqlite3.Row iterates VALUES, not keys (unlike a dict) -- .keys() is
    # required here, ruff's SIM118 doesn't know that.
    return QueuedIssue(**{k: row[k] for k in row.keys()})  # noqa: SIM118


class IssueQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            self._apply_migrations(conn)

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(issues)")}
        for column, ddl in _MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE issues ADD COLUMN {column} {ddl}")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def has_seen_repo(self, repo: str) -> bool:
        """True if any issue for ``repo`` has ever been recorded.

        Used to detect a repo's very first poll pass, so a fresh watchlist
        entry can be silently backfilled instead of firing one notification
        per pre-existing open issue.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM issues WHERE repo=? LIMIT 1", (repo,)
            ).fetchone()
        return row is not None

    def record_if_new(
        self,
        repo: str,
        number: int,
        title: str,
        url: str,
        *,
        source: str = "watchlist",
        bounty_label: str | None = None,
    ) -> bool:
        """Insert an issue if unseen. Returns True iff it was newly inserted.

        ``source`` distinguishes where a queued issue came from (the curated
        ``watchlist`` repo poll vs. a ``paid_bounty`` global search) purely
        for display/filtering -- it never changes queue behavior.
        """
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO issues "
                    "(repo, number, title, url, first_seen, source, bounty_label) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (repo, number, title, url, datetime.now(UTC).isoformat(), source, bounty_label),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_notified(self, url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE issues SET status='notified', notified_at=? WHERE url=?",
                (datetime.now(UTC).isoformat(), url),
            )

    def mark_claimed(self, url: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE issues SET status='claimed' WHERE url=?", (url,))

    def mark_pr_open(self, url: str, pr_url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE issues SET status='pr_open', pr_url=? WHERE url=?", (pr_url, url)
            )

    def get(self, url: str) -> QueuedIssue | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM issues WHERE url=?", (url,)).fetchone()
        return _row_to_issue(row) if row else None

    def pending(self) -> list[QueuedIssue]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM issues WHERE status IN ('new', 'notified') "
                "ORDER BY first_seen ASC"
            ).fetchall()
        return [_row_to_issue(r) for r in rows]
