"""SQLite analytics store for behavioral events — the flywheel's query substrate.

events.jsonl stays the fail-loud ingest journal / audit tail; this store is the
queryable copy with typed columns so cohort/funnel/join work gets predicate
pushdown instead of whole-file JSONL scans (audit §三 S3). stdlib sqlite3 only:
zero new dependencies, zero extra hardware footprint (per-device pricing
constraint).

Failure contract mirrors EventLog.append: never raises into the dispatch loop,
always loud in logs. Habit events are stored UNREDACTED by design — local-only,
per the privacy-investment freeze (audit §十); the LLM egress keeps its own
redaction independently.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("glimpse.store")

SCHEMA_VERSION = 1

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    window_title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL
);
"""

_COLUMNS = (
    "schema_version",
    "ts",
    "kind",
    "app",
    "window_title",
    "url",
    "session_id",
    "payload",
)


class BehaviorStore:
    """Append + query over the behavioral-events table.

    session_id is reserved for the P7.3 sessionizer; "" until then.
    """

    def __init__(self, path: Path) -> None:
        self._conn: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        except (OSError, sqlite3.Error) as exc:
            # Degraded, not dead: the journal (events.jsonl) still records
            # everything; analytics queries return empty until restart.
            log.warning("behavior store disabled (%s): %s", path, exc)

    def append(
        self,
        *,
        kind: str,
        ts: str,
        app: str = "",
        window_title: str = "",
        url: str = "",
        session_id: str = "",
        payload: dict[str, object] | None = None,
    ) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO events"
                " (schema_version, ts, kind, app, window_title, url, session_id, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    ts,
                    kind,
                    app,
                    window_title,
                    url,
                    session_id,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("behavior store append failed: %s", exc)

    def query(
        self, *, kind: str | None = None, limit: int = 1000
    ) -> list[dict[str, object]]:
        if self._conn is None:
            return []
        try:
            sql = f"SELECT {', '.join(_COLUMNS)} FROM events"
            args: tuple[object, ...] = ()
            if kind is not None:
                sql += " WHERE kind = ?"
                args = (kind,)
            sql += " ORDER BY id LIMIT ?"
            args = (*args, limit)
            rows = self._conn.execute(sql, args).fetchall()
            return [dict(zip(_COLUMNS, row, strict=True)) for row in rows]
        except sqlite3.Error as exc:
            log.warning("behavior store query failed: %s", exc)
            return []

    def save_session_state(self, state: dict[str, object]) -> None:
        """Persist the Sessionizer's VERBATIM snapshot (single row). Rebuilding
        state from event rows was a lossy projection — it could not hold an
        open implicit run and the ended-run grace anchor at once, and row
        timestamps diverge from the live clock (merge-gate findings)."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO session_state (id, state) VALUES (1, ?)"
                " ON CONFLICT(id) DO UPDATE SET state = excluded.state",
                (json.dumps(state, ensure_ascii=False),),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("behavior store session-state save failed: %s", exc)

    def load_session_state(self) -> dict[str, object] | None:
        """The last saved Sessionizer snapshot, or None (absent/corrupt/disabled)."""
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT state FROM session_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            state = json.loads(row[0])
            return state if isinstance(state, dict) else None
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            log.warning("behavior store session-state load failed: %s", exc)
            return None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
