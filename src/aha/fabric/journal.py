"""The audit trail: what was asked, what was approved, what it cost, what happened.

Deliberately SQLite and deliberately boring. A run that cannot be explained
afterwards is not a run you can delegate.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    pack        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    autonomy    TEXT NOT NULL,
    goal        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    usd         REAL NOT NULL DEFAULT 0.0,
    detail      TEXT,
    branch      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL REFERENCES runs(id),
    at      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id);
"""


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """Bring a journal written by an older version up to the current schema."""
    present = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
    for column, kind in (('branch', 'TEXT'),):
        if column not in present:
            db.execute(f'ALTER TABLE runs ADD COLUMN {column} {kind}')


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


@dataclass
class Journal:
    """Append-only record of one agent run."""

    path: Path
    run_id: str

    @classmethod
    def open(cls, path: Path, *, spec: Any) -> Journal:
        """Start a run and return its journal. Creates the database if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        run_id = f'{spec.name}-{uuid.uuid4().hex[:8]}'
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA)
            _add_missing_columns(db)
            db.execute(
                'INSERT INTO runs (id, task, pack, kind, autonomy, goal, workspace, started_at)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    run_id,
                    spec.name,
                    spec.pack,
                    spec.kind,
                    str(spec.autonomy),
                    spec.goal,
                    str(spec.resolved_workspace),
                    _now(),
                ),
            )
            db.commit()
        return cls(path=path, run_id=run_id)

    def set_branch(self, branch: str) -> None:
        """Record where this run's changes live, so a reviewer can find the diff."""
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE runs SET branch = ? WHERE id = ?', (branch, self.run_id))
            db.commit()

    def record(self, event: str, /, **payload: Any) -> None:
        """Append one event. `event` is positional so payload keys never collide."""
        blob = json.dumps(payload, default=str, ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                'INSERT INTO events (run_id, at, kind, payload) VALUES (?, ?, ?, ?)',
                (self.run_id, _now(), event, blob),
            )
            db.commit()

    def finish(self, *, status: str, usd: float = 0.0, detail: str = '') -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                'UPDATE runs SET ended_at = ?, status = ?, usd = ?, detail = ? WHERE id = ?',
                (_now(), status, usd, detail, self.run_id),
            )
            db.commit()

    def events(self) -> list[tuple[str, str, dict[str, Any]]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                'SELECT at, kind, payload FROM events WHERE run_id = ? ORDER BY id',
                (self.run_id,),
            ).fetchall()
        return [(at, kind, json.loads(payload)) for at, kind, payload in rows]
