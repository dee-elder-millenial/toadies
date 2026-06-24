from datetime import datetime, timedelta, timezone

from toadies import trust
from toadies.store import Store


def test_competency_and_grades_persist_across_connections(tmp_path):
    db = tmp_path / "toadies.db"

    s = Store(db)
    state = trust.record_grade(s, "gremlin", "pytest", 0.9, prompt_hash="abc", output_hash="def")
    assert state.ema == 0.9
    assert trust.competency(s, "gremlin", "pytest").samples == 1
    s.close()

    # reopen a fresh connection: the data is durable
    s2 = Store(db)
    reread = trust.competency(s2, "gremlin", "pytest")
    assert reread.ema == 0.9
    assert reread.samples == 1
    assert reread.leash_level == "probation"
    s2.close()


def test_grades_are_appended_for_the_audit_trail(tmp_path):
    s = Store(tmp_path / "toadies.db")
    for _ in range(3):
        trust.record_grade(s, "gremlin", "pytest", 0.8)
    assert s.grade_count("gremlin", "pytest") == 3
    s.close()


def test_list_events_normalizes_iso8601_created_at_cutoff(tmp_path):
    db = tmp_path / "toadies.db"
    s = Store(db)
    s.insert_event(
        id="evt-1",
        event_type="toadie_interjection",
        toadie="toadie-a",
        metadata_json='{"message":"hello"}',
    )
    now = datetime.now(timezone.utc)

    rows_before = s.list_events("toadie_interjection", limit=5, since_created_at=(now - timedelta(minutes=1)).isoformat())
    assert len(rows_before) == 1

    rows_after = s.list_events("toadie_interjection", limit=5, since_created_at=(now + timedelta(days=1)).isoformat())
    assert len(rows_after) == 0

    s.close()
