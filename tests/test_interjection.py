import json
from datetime import datetime, timedelta, timezone

from toadies import interjection


class FakeStore:
    def __init__(self):
        self.competency = {}
        self.events = []

    def with_competency(self, toadie, task_type, leash_level, ema, samples):
        self.competency[(toadie, task_type)] = {
            "leash_level": leash_level,
            "ema": ema,
            "samples": samples,
        }

    def get_competency(self, toadie, task_type):
        return self.competency.get((toadie, task_type))

    def insert_grade(self, **kw):
        return None

    def upsert_competency(self, toadie, task_type, ema, samples, leash_level):
        self.competency[(toadie, task_type)] = {
            "ema": ema,
            "samples": samples,
            "leash_level": leash_level,
        }

    def insert_event(
        self,
        *,
        id,
        event_type,
        toadie=None,
        cwd=None,
        input_hash=None,
        output_hash=None,
        metadata_json=None,
        session_id=None,
        turn_id=None,
    ):
        metadata = metadata_json
        if isinstance(metadata_json, str):
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = {}
        created_at = None
        if isinstance(metadata, dict):
            created_at = metadata.get("created_at")
        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()
        self.events.append(
            {
                "id": id,
                "event_type": event_type,
                "toadie": toadie,
                "metadata_json": metadata_json if isinstance(metadata_json, str) else json.dumps(metadata_json),
                "created_at": created_at,
            }
        )
        self.last_insert = {
            "id": id,
            "event_type": event_type,
            "toadie": toadie,
            "metadata_json": metadata_json,
        }

    def list_events(self, event_type, limit=50, since_created_at=None):
        rows = [e for e in self.events if e["event_type"] == event_type]
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        if since_created_at is not None:
            since = datetime.fromisoformat(since_created_at.replace("Z", "+00:00"))
            filtered = []
            for row in rows:
                row_ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if row_ts >= since:
                    filtered.append(row)
            rows = filtered
        return rows[:limit]

    def close(self):
        return None


def test_interjection_can_interrupt_when_trusted_and_guard_passes(monkeypatch):
    store = FakeStore()
    store.with_competency("toadette", "bugfix", "trusted", 0.95, 30)
    monkeypatch.setenv("TOADIES_TOADETTE_GRADUATED", "1")

    out = interjection.post_interjection(
        "toadette",
        "bugfix",
        message="found unsafe call in path",
        task_context={"file": "src/main.py"},
        requested_delivery="auto",
        urgency="high",
        store_cls=lambda *_args, **_kwargs: store,
    )

    assert out["ok"] is True
    assert out["delivery"] == interjection.DELIVERY_INTERRUPT
    payload = json.loads(store.last_insert["metadata_json"])
    assert payload["delivery"] == interjection.DELIVERY_INTERRUPT
    assert payload["trust"]["leash_level"] == "trusted"


def test_interjection_rejected_if_trust_too_low_for_any_non_append_path():
    store = FakeStore()
    store.with_competency("toadie", "pytest", "probation", 0.25, 1)

    out = interjection.post_interjection(
        "toadie",
        "pytest",
        message="possible leak in test fixture",
        requested_delivery="auto",
        urgency="medium",
        store_cls=lambda *_args, **_kwargs: store,
    )

    assert out["ok"] is False
    assert out["delivery"] is None
    assert out["trust"]["leash_level"] == "probation"


def test_toadette_requires_graduation_before_interrupt(monkeypatch):
    store = FakeStore()
    store.with_competency("toadette", "pytest", "trusted", 0.95, 30)

    out = interjection.post_interjection(
        "toadette",
        "pytest",
        message="non-graduated interrupt request",
        requested_delivery="interrupt",
        urgency="high",
        store_cls=lambda *_args, **_kwargs: store,
    )

    assert out["ok"] is True
    assert out["delivery"] == interjection.DELIVERY_APPEND
    assert out["guarded"] is True
    assert "requires explicit graduation" in out["reason"].lower()


def test_interruption_guard_blocks_frequent_interrupts_for_same_toadie(monkeypatch):
    store = FakeStore()
    store.with_competency("toadette", "pytest", "trusted", 0.95, 30)
    monkeypatch.setenv("TOADIES_TOADETTE_GRADUATED", "1")
    first_ts = datetime(2026, 6, 23, 11, 10, 0, tzinfo=timezone.utc)

    first = interjection.post_interjection(
        "toadette",
        "pytest",
        message="first finding",
        requested_delivery="interrupt",
        urgency="high",
        now=first_ts,
        store_cls=lambda *_args, **_kwargs: store,
    )

    assert first["ok"] is True
    assert first["delivery"] == interjection.DELIVERY_INTERRUPT

    second = interjection.post_interjection(
        "toadette",
        "pytest",
        message="second finding",
        requested_delivery="interrupt",
        urgency="high",
        now=first_ts + timedelta(seconds=20),
        store_cls=lambda *_args, **_kwargs: store,
    )

    assert second["ok"] is True
    assert second["delivery"] == interjection.DELIVERY_APPEND
    assert second["guarded"] is True
    assert "cooldown" in second["reason"].lower()


def test_interruption_guard_burst_cap_does_not_block_critical_interrupts(monkeypatch):
    store = FakeStore()
    store.with_competency("toadette", "pytest", "trusted", 0.95, 30)
    monkeypatch.setenv("TOADIES_TOADETTE_GRADUATED", "1")

    # Fill the burst window with pre-existing interrupts from another toadie.
    now = datetime(2026, 6, 23, 11, 15, 0, tzinfo=timezone.utc)
    for i in range(interjection.INTERRUPT_BURST_LIMIT):
        old_ts = (now - timedelta(seconds=i * 10)).isoformat()
        store.events.append(
            {
                "id": f"e{i}",
                "event_type": interjection.INTERJECTION_EVENT,
                "toadie": "toadie-other",
                "metadata_json": json.dumps(
                    {
                        "delivery": interjection.DELIVERY_INTERRUPT,
                        "toadie": "toadie-other",
                        "task_type": "pytest",
                        "created_at": old_ts,
                    }
                ),
                "created_at": old_ts,
            }
        )

    out = interjection.post_interjection(
        "toadette",
        "pytest",
        message="critical issue",
        requested_delivery="interrupt",
        urgency="critical",
        now=now + timedelta(seconds=1),
        store_cls=lambda *_args, **_kwargs: store,
        interrupt_burst_window_seconds=300,
        interrupt_burst_limit=interjection.INTERRUPT_BURST_LIMIT,
    )

    assert out["ok"] is True
    assert out["delivery"] == interjection.DELIVERY_INTERRUPT


def test_list_interjections_parses_metadata_and_filters(monkeypatch):
    store = FakeStore()
    store.events.extend(
        [
            {
                "id": "e1",
                "event_type": interjection.INTERJECTION_EVENT,
                "toadie": "toadette",
                "metadata_json": json.dumps(
                    {
                        "toadie": "toadette",
                        "task_type": "pytest",
                        "delivery": interjection.DELIVERY_APPEND,
                        "urgency": "low",
                        "message": "note one",
                    }
                ),
                "created_at": "2026-06-23T12:00:00+00:00",
            },
            {
                "id": "e2",
                "event_type": interjection.INTERJECTION_EVENT,
                "toadie": "scout",
                "metadata_json": json.dumps(
                    {
                        "toadie": "scout",
                        "task_type": "pytest",
                        "delivery": interjection.DELIVERY_INTERRUPT,
                        "urgency": "critical",
                        "message": "note two",
                    }
                ),
                "created_at": "2026-06-23T12:01:00+00:00",
            },
        ]
    )

    def fake_store(_db_path):
        return store

    monkeypatch.setattr(interjection, "Store", fake_store)

    out = interjection.list_interjections(
        db_path="ignore.db",
        limit=10,
        toadie="scout",
        delivery=interjection.DELIVERY_INTERRUPT,
    )

    assert len(out) == 1
    assert out[0]["event_id"] == "e2"
    assert out[0]["toadie"] == "scout"
    assert out[0]["message"] == "note two"
    assert out[0]["raw"]["urgency"] == "critical"


def test_list_interjections_filters_by_created_at_cutoff(monkeypatch):
    store = FakeStore()
    store.events.extend(
        [
            {
                "id": "old",
                "event_type": interjection.INTERJECTION_EVENT,
                "toadie": "toadette",
                "metadata_json": json.dumps(
                    {
                        "toadie": "toadette",
                        "task_type": "pytest",
                        "delivery": interjection.DELIVERY_APPEND,
                        "urgency": "low",
                        "message": "too old",
                    }
                ),
                "created_at": "2026-06-23T10:00:00+00:00",
            },
            {
                "id": "fresh",
                "event_type": interjection.INTERJECTION_EVENT,
                "toadie": "toadette",
                "metadata_json": json.dumps(
                    {
                        "toadie": "toadette",
                        "task_type": "pytest",
                        "delivery": interjection.DELIVERY_APPEND,
                        "urgency": "low",
                        "message": "new enough",
                    }
                ),
                "created_at": "2026-06-23T12:00:00+00:00",
            },
        ]
    )

    def fake_store(_db_path):
        return store

    monkeypatch.setattr(interjection, "Store", fake_store)

    out = interjection.list_interjections(
        db_path="ignore.db",
        limit=10,
        since_created_at="2026-06-23T11:00:00+00:00",
    )

    assert len(out) == 1
    assert out[0]["event_id"] == "fresh"
    assert out[0]["message"] == "new enough"
