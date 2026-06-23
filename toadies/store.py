"""SQLite store for the Toadies sidecar.

Applies the canonical schema (handoff tables + trust tables) on open and provides the
small read/write interface the trust engine needs. Kept deliberately thin so the engine
can be unit-tested against an in-memory fake (see tests/test_trust.py).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Store:
    def __init__(self, db_path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_PATH.read_text())
        self._conn.commit()

    # --- interface used by trust.py ---------------------------------------
    def get_competency(self, toadie, task_type):
        row = self._conn.execute(
            "select ema, samples, leash_level from competency where toadie=? and task_type=?",
            (toadie, task_type),
        ).fetchone()
        if row is None:
            return None
        return {"ema": row["ema"], "samples": row["samples"], "leash_level": row["leash_level"]}

    def upsert_competency(self, toadie, task_type, ema, samples, leash_level):
        self._conn.execute(
            """insert into competency (toadie, task_type, ema, samples, leash_level, updated_at)
               values (?, ?, ?, ?, ?, current_timestamp)
               on conflict(toadie, task_type) do update set
                 ema=excluded.ema, samples=excluded.samples,
                 leash_level=excluded.leash_level, updated_at=current_timestamp""",
            (toadie, task_type, ema, samples, leash_level),
        )
        self._conn.commit()

    def insert_grade(self, *, id, toadie, task_type, score, source,
                     prompt_hash=None, output_hash=None, event_id=None):
        self._conn.execute(
            """insert into grades
               (id, toadie, task_type, score, source, prompt_hash, output_hash, event_id)
               values (?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, toadie, task_type, score, source, prompt_hash, output_hash, event_id),
        )
        self._conn.commit()

    # --- helpers ----------------------------------------------------------
    def grade_count(self, toadie, task_type):
        return self._conn.execute(
            "select count(*) from grades where toadie=? and task_type=?",
            (toadie, task_type),
        ).fetchone()[0]

    def all_competency(self):
        rows = self._conn.execute(
            "select toadie, task_type, ema, samples, leash_level from competency "
            "order by toadie, task_type"
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()
