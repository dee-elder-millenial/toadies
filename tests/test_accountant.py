from toadies import accountant, trust
from toadies.store import Store


def test_status_reports_the_trust_ladder(tmp_path):
    s = Store(tmp_path / "toadies.db")
    # gremlin earns trust on pytest
    for _ in range(25):
        trust.record_grade(s, "gremlin", "pytest", 0.95)
    # but is still on probation for webpack
    trust.record_grade(s, "gremlin", "webpack", 0.7)

    rows = accountant.status(s)
    by_key = {(r["toadie"], r["task_type"]): r for r in rows}
    assert by_key[("gremlin", "pytest")]["leash_level"] == "trusted"
    assert by_key[("gremlin", "webpack")]["leash_level"] == "probation"

    text = accountant.render_status(s)
    assert "gremlin/pytest" in text
    assert "TRUSTED" in text
    assert "gremlin/webpack" in text
    assert "PROBATION" in text


def test_render_status_handles_empty(tmp_path):
    s = Store(tmp_path / "toadies.db")
    text = accountant.render_status(s)
    assert "no toadies" in text.lower()
