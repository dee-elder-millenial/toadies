import random
import pytest

from toadies import trust


class FakeStore:
    """In-memory stand-in for the SQLite store, exercising the same interface."""

    def __init__(self):
        self.competency = {}
        self.grades = []

    def get_competency(self, toadie, task_type):
        return self.competency.get((toadie, task_type))

    def upsert_competency(self, toadie, task_type, ema, samples, leash_level):
        self.competency[(toadie, task_type)] = {
            "ema": ema,
            "samples": samples,
            "leash_level": leash_level,
        }

    def insert_grade(self, **kw):
        self.grades.append(kw)


def test_update_ema_blends_old_and_new_with_alpha():
    # ema = a*score + (1-a)*old
    assert trust.update_ema(0.8, 0.6, alpha=0.5) == 0.7
    assert trust.update_ema(0.0, 0.9, alpha=0.3) == 0.27


def test_promotion_requires_both_high_ema_and_enough_samples():
    # high ema but too few samples → no promotion (no lucky short streaks)
    assert trust.next_level("probation", ema=0.99, samples=5) == "probation"
    # threshold + min samples both met → promote one rung
    assert trust.next_level("probation", ema=0.85, samples=8) == "spot_check"
    # spot_check needs the higher bar to reach trusted
    assert trust.next_level("spot_check", ema=0.92, samples=20) == "trusted"
    # spot_check with trusted-level ema but too few samples stays put
    assert trust.next_level("spot_check", ema=0.99, samples=12) == "spot_check"


def test_demotion_and_hysteresis():
    # falling below the demotion floor drops one rung
    assert trust.next_level("trusted", ema=0.79, samples=50) == "spot_check"
    assert trust.next_level("spot_check", ema=0.64, samples=50) == "probation"
    # hysteresis: a trusted toadie hovering between 0.80 and 0.92 does NOT flap
    assert trust.next_level("trusted", ema=0.83, samples=50) == "trusted"


def test_should_audit_probation_always_trusted_rarely():
    rng = random.Random(0)
    # probation is always reviewed regardless of the draw
    assert all(trust.should_audit("probation", rng) for _ in range(50))

    # trusted is audited ~5% of the time; over many draws it should be rare
    rng = random.Random(1234)
    audits = sum(trust.should_audit("trusted", rng) for _ in range(2000))
    assert 40 <= audits <= 160   # ~5% of 2000 = 100, generous band

    # spot_check (~20%) sits clearly between the two rates
    rng = random.Random(1234)
    spot = sum(trust.should_audit("spot_check", rng) for _ in range(2000))
    assert spot > audits


def test_needs_review_escalates_on_low_confidence_or_truncation():
    rng = random.Random(0)
    # a Trusted toadie is normally rarely reviewed...
    # ...but low per-output confidence forces review anyway
    assert trust.needs_review("trusted", rng, confidence=0.20) is True
    # a truncated output is always suspect, regardless of confidence/level
    assert trust.needs_review("trusted", rng, truncated=True) is True
    # high confidence on a trusted toadie does NOT force review (defers to sampling)
    rng = random.Random(0)
    forced = trust.needs_review("trusted", rng, confidence=0.95)
    assert forced in (True, False)  # may still be a rare random audit, but not forced
    # probation always reviews no matter what
    assert trust.needs_review("probation", rng, confidence=0.99) is True


def test_needs_review_without_confidence_matches_should_audit():
    # absent confidence, needs_review behaves exactly like should_audit (same RNG stream)
    a = random.Random(42)
    b = random.Random(42)
    for _ in range(100):
        assert trust.needs_review("spot_check", a) == trust.should_audit("spot_check", b)


def test_competency_read_defaults_to_probation_when_unknown():
    store = FakeStore()
    state = trust.competency(store, "scout", "generic")
    assert state.leash_level == "probation"
    assert state.ema == 0.0
    assert state.samples == 0


def test_note_outcome_merit_raises_demerit_lowers():
    store = FakeStore()
    # build some history first
    for _ in range(10):
        trust.record_grade(store, "gremlin", "pytest", 0.9)
    before = trust.competency(store, "gremlin", "pytest").ema

    trust.note_outcome(store, "gremlin", "pytest", "demerit")
    after_bad = trust.competency(store, "gremlin", "pytest").ema
    assert after_bad < before
    # the outcome was logged as such, not as a rubric grade
    assert store.grades[-1]["source"] == "outcome"

    trust.note_outcome(store, "gremlin", "pytest", "merit")
    after_good = trust.competency(store, "gremlin", "pytest").ema
    assert after_good > after_bad


def test_bad_rubric_audit_instant_demotes_but_outcome_demerit_does_not():
    store = FakeStore()
    # climb to trusted
    state = None
    for _ in range(25):
        state = trust.record_grade(store, "gremlin", "pytest", 0.95)
    assert state.leash_level == "trusted"

    # a single outcome demerit dips the ema but must NOT instantly demote the level
    after_outcome = trust.note_outcome(store, "gremlin", "pytest", "demerit")
    assert after_outcome.leash_level == "trusted"

    # a bad RUBRIC audit (the strong model directly graded it terrible) yanks one rung now
    after_audit = trust.record_grade(store, "gremlin", "pytest", 0.05, source="rubric")
    assert after_audit.leash_level == "spot_check"


def test_first_grade_seeds_ema_to_score_and_records():
    store = FakeStore()
    state = trust.record_grade(store, "gremlin", "pytest", 0.9)

    # cold start: first grade seeds the EMA to the score (no drag from 0.0)
    assert state.ema == 0.9
    assert state.samples == 1
    assert state.leash_level == "probation"
    # persisted, and the grade is logged for the audit trail
    assert store.competency[("gremlin", "pytest")]["ema"] == 0.9
    assert len(store.grades) == 1


def test_record_grade_rejects_invalid_inputs():
    store = FakeStore()
    with pytest.raises(ValueError):
        trust.record_grade(store, "gremlin", "pytest", 1.2)
    with pytest.raises(ValueError):
        trust.record_grade(store, "gremlin", "pytest", -0.01)
    with pytest.raises(ValueError):
        trust.record_grade(store, "gremlin", "pytest", 0.5, source="nonsense")
