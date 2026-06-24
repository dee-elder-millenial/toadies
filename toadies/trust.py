"""Trust engine — competency scoring and leash (review-rate) gating.

Deterministic and model-free: a model only ever *produces* grades, which enter here as
plain numbers. See docs/superpowers/specs/2026-06-23-toadies-trust-loop-design.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# --- Tunable defaults (live in config in production) ---------------------------
ALPHA = 0.30
# Outcome (usage) signals are indirect/noisier than rubric grades, so they move the
# EMA more gently — "secondary" made concrete.
OUTCOME_ALPHA = 0.10

LEVELS = ("probation", "spot_check", "trusted")

# Promotion gates: to LEAVE the given level upward you need ema >= threshold AND
# samples >= min_samples.
_PROMOTE = {
    "probation": {"ema": 0.85, "samples": 8},
    "spot_check": {"ema": 0.92, "samples": 20},
}
# Demotion thresholds (hysteresis): the gap below the promotion bar prevents flapping.
_DEMOTE = {
    "trusted": 0.80,     # trusted -> spot_check below this
    "spot_check": 0.65,  # spot_check -> probation below this
}

# Review (audit) probability per leash level.
_AUDIT_P = {
    "probation": 1.00,
    "spot_check": 0.20,
    "trusted": 0.05,
}

# A single audit at or below this score forces an immediate one-level demotion.
INSTANT_DEMOTE_SCORE = 0.30

# Outcome signals map to grade scores (recorded with source='outcome').
_OUTCOME_SCORES = {"merit": 0.90, "demerit": 0.20}

# Per-output confidence below this forces paid review regardless of leash level.
CONFIDENCE_FLOOR = 0.55

_VALID_SOURCES = {"rubric", "outcome"}


def _coerce_score(score):
    try:
        score = float(score)
    except (TypeError, ValueError):
        raise ValueError(f"score must be a number, got {type(score).__name__}")

    if score < 0.0 or score > 1.0:
        raise ValueError(f"score must be in [0.0, 1.0], got {score!r}")
    return score


def _coerce_source(source):
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES!r}, got {source!r}")
    return source


def update_ema(old_ema, score, alpha):
    return alpha * score + (1 - alpha) * old_ema


def _demote_one(level):
    i = LEVELS.index(level)
    return LEVELS[max(0, i - 1)]


def _promote_one(level):
    i = LEVELS.index(level)
    return LEVELS[min(len(LEVELS) - 1, i + 1)]


def should_audit(level, rng):
    """Return True if this output should be reviewed by the paid model.

    `rng` is injected (a random.Random) so review rates are testable deterministically.
    """
    p = _AUDIT_P[level]
    if p >= 1.0:
        return True
    return rng.random() < p


def needs_review(level, rng, *, confidence=None, truncated=False):
    """Whether this specific output should get paid review.

    The leash `level` sets the default rate, but a single output can override it *upward*:
    a truncated answer or a low per-output confidence forces review regardless of level.
    The override only ever raises review, never lowers it.
    """
    if truncated:
        return True
    if confidence is not None and confidence < CONFIDENCE_FLOOR:
        return True
    return should_audit(level, rng)


def next_level(current, ema, samples):
    """Gradual one-step transition based on the smoothed score and sample count."""
    gate = _PROMOTE.get(current)
    if gate is not None and ema >= gate["ema"] and samples >= gate["samples"]:
        return _promote_one(current)
    floor = _DEMOTE.get(current)
    if floor is not None and ema < floor:
        return _demote_one(current)
    return current


@dataclass
class CompetencyState:
    toadie: str
    task_type: str
    ema: float
    samples: int
    leash_level: str


def record_grade(store, toadie, task_type, score, source="rubric",
                 *, prompt_hash=None, output_hash=None, event_id=None, alpha=ALPHA):
    """Record one grade, update the smoothed score, and recompute the leash level."""
    score = _coerce_score(score)
    source = _coerce_source(source)
    prev = store.get_competency(toadie, task_type)
    if prev is None:
        ema = score              # cold start: seed to the first score
        samples = 1
        prev_level = "probation"
    else:
        ema = update_ema(prev["ema"], score, alpha)
        samples = prev["samples"] + 1
        prev_level = prev["leash_level"]

    if source == "rubric" and score <= INSTANT_DEMOTE_SCORE:
        # one bad *audit* (strong model graded it terrible) yanks the leash now.
        # Indirect outcome signals only nudge the EMA; they never instant-demote.
        level = _demote_one(prev_level)
    else:
        level = next_level(prev_level, ema, samples)

    store.insert_grade(
        id=str(uuid.uuid4()), toadie=toadie, task_type=task_type, score=score,
        source=source, prompt_hash=prompt_hash, output_hash=output_hash, event_id=event_id,
    )
    store.upsert_competency(toadie, task_type, ema, samples, level)
    return CompetencyState(toadie, task_type, ema, samples, level)


def competency(store, toadie, task_type):
    """Read current competency; unknown (toadie, task_type) defaults to probation."""
    row = store.get_competency(toadie, task_type)
    if row is None:
        return CompetencyState(toadie, task_type, 0.0, 0, "probation")
    return CompetencyState(toadie, task_type, row["ema"], row["samples"], row["leash_level"])


def note_outcome(store, toadie, task_type, signal, **kw):
    """Record a downstream usage signal ('merit'|'demerit') as an outcome grade."""
    score = _OUTCOME_SCORES[signal]
    return record_grade(store, toadie, task_type, score, source="outcome",
                        alpha=OUTCOME_ALPHA, **kw)
