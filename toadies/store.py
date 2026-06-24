"""SQLite store for the Toadies sidecar.

Applies the canonical schema (handoff tables + trust tables) on open and provides the
small read/write interface the trust engine needs. Kept deliberately thin so the engine
can be unit-tested against an in-memory fake (see tests/test_trust.py).
"""

from __future__ import annotations

import sqlite3
import json
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

    def insert_event(self, *, id, event_type, toadie=None, cwd=None,
                     input_hash=None, output_hash=None, metadata_json=None,
                     session_id=None, turn_id=None):
        if metadata_json is None:
            metadata_json = "{}"
        if not isinstance(metadata_json, str):
            metadata_json = json.dumps(metadata_json)
        self._conn.execute(
            """insert into events
               (id, session_id, turn_id, event_type, toadie, cwd, input_hash, output_hash, metadata_json)
               values (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id,
                session_id,
                turn_id,
                event_type,
                toadie,
                cwd,
                input_hash,
                output_hash,
                metadata_json,
            ),
        )
        self._conn.commit()

    def list_events(self, event_type, limit=50, since_created_at=None):
        def _normalize_since(value):
            if value is None:
                return None
            value = str(value).replace("Z", "").replace("T", " ")
            if "+" in value:
                value = value.split("+", 1)[0]
            return value

        sql = ("select id, event_type, toadie, cwd, input_hash, output_hash, metadata_json, created_at "
               "from events where event_type=?")
        params = [event_type]
        if since_created_at is not None:
            sql += " and created_at >= ?"
            params.append(_normalize_since(since_created_at))
        sql += " order by created_at desc limit ?"
        params.append(limit)
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

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
