"""Accountant — surfaces the trust ladder in plain language.

Today it reports the *real* data: each Toadie's competency and leash level per task
type. The J-curve token accounting (invested vs avoided) needs per-review token costs
that only exist once the MCP/hook integration is built, so it is intentionally not
fabricated here — see docs/superpowers/specs/2026-06-23-toadies-trust-loop-design.md.
"""

from __future__ import annotations


def status(store):
    """Structured trust-ladder rows for every known (toadie, task_type)."""
    return store.all_competency()


def render_status(store):
    rows = status(store)
    if not rows:
        return "No Toadies have been graded yet — every Toadie starts on probation."
    lines = ["Toadie trust ladder:"]
    for r in rows:
        lines.append(
            f"  {r['toadie']}/{r['task_type']:<10} {r['leash_level'].upper():<10} "
            f"ema {r['ema']:.2f}  (n={r['samples']})"
        )
    return "\n".join(lines)
