# Design — Toadies Competency & Trust Loop

**Date:** 2026-06-23
**Status:** Approved (design); implementation plan pending
**Project:** Toadies (local-first sidecar for Codex/Claude) — `/cloud-mirror/toadies/`
**Builds on:** the shipped Gremlin slice (`toadies/gremlin.py`, `toadies/cli.py`) and the handoff
spec at `/cloud-mirror/robot-toadies-handoff/robot-toadies-handoff/` (SPEC-001, `database-schema.sql`).

## Summary

Add a feedback loop in which the **strong, paid AI** (Codex "Robot" or Claude) acts as a
coach/judge for the **weak, local Toadies**. As a Toadie proves competent at a given kind of
work, it earns a longer "leash" — meaning the paid AI reviews its output *less often*, which is
what produces token savings. Trust is earned slowly and lost quickly.

This deliberately *increases* paid-AI spend up front (the paid AI grades output during the
probation phase) in exchange for compounding savings later. The economics are a J-curve, and the
Accountant makes that curve visible so the user can watch a Toadie cross breakeven rather than
take it on faith.

## Decisions (locked during brainstorming)

1. **"Training" = collect now, fine-tune later.** No model weights change in this work. The loop
   maintains a trust score plus stored few-shot exemplars. Separately, graded-*good*
   `(input → output)` pairs are logged to `dataset.jsonl` so an *optional, future* LoRA/fine-tune
   stays on the table. No fine-tuning is built now (and it would fight the RX 580 / Polaris
   GPU's CUDA-blindness anyway).
2. **Leash = review rate, not power.** Earning trust buys *less paid-AI review*, never the
   ability to take destructive actions. All Toadies stay read-only (consistent with SPEC-001's
   MVP non-goals). Three rungs: `probation` → `spot_check` → `trusted`.
3. **The judge is always the strong model.** A Toadie never grades itself; self-assessment by a
   1B local model is the blind leading the blind. The integrity of the whole loop depends on the
   capable paid model being the grader.
4. **Grade = rubric (primary) + outcome (secondary).** The rubric score is a near-free byproduct
   of review that is *already happening* during probation/audits. The outcome signal is derived
   from downstream usage. Both feed one smoothed score (EMA).
5. **Architecture: extend the Accountant + a shared `trust` module** (chosen over a dedicated
   "Foreman" coordinator). The loop slots into a role SPEC-001 already defines, keeps new code
   small and deterministic, and is fully testable without any model in the loop.
6. **Trust has two axes: track record AND per-output confidence.** Competency (the EMA) is the
   Toadie's *historical* track record on a task type and sets the *default* review rate. A
   separate *per-output* confidence signal captures how shaky *this particular* answer was. Low
   confidence (or a truncated output) escalates that one output to paid review **regardless of
   leash level** — a Trusted Toadie that is uncertain this time raises its own hand. Crucially,
   this confidence comes from the **local** model's token logprobs (LocalAI / llama.cpp expose
   them), not from the model's prose, which is not a calibrated signal. (The paid Claude judge
   does not expose logprobs; where a paid-side confidence is ever needed, use `finish_reason` /
   self-consistency instead.)

## Components

All new code is deterministic and model-free (a model only ever *produces grades*, which enter
the system as plain numbers).

| Module | Responsibility | Depends on |
|---|---|---|
| `store.py` | Open SQLite at the config'd `db_path`; apply the existing handoff schema **plus** the two new trust tables; typed read/write helpers. Sits behind a small interface so the engine can be tested against an in-memory fake. | sqlite3, config |
| `trust.py` | The engine. `record_grade()`, `competency()`, `next_level()`, `should_audit()`, `needs_review()`, `note_outcome()`. Pure logic over the store. **Never calls a model.** | `store.py` |
| `accountant.py` | Surface competency + leash levels in plain language; compute the J-curve (review tokens spent vs avoided). Powers `toadiectl accountant status`. | `trust.py`, `store.py` |
| `dataset.py` | Append graded-good `(input → output)` pairs to `~/.local/share/toadies/dataset.jsonl`. | config |

**Integration points (designed now, built after the core):**

- MCP tool results carry a `review` hint derived from the Toadie's current competency
  (`probation` → "please review"; `trusted` → "safe to use, ~1 in 20 audited").
- `toadiectl grade` command / MCP `submit_grade` tool: how the paid model posts its rubric score
  back into the loop.

## Data model

Two new tables, **extending** (not replacing) the handoff `database-schema.sql`. Grade and
compression events also continue to be logged in the existing `events` table.

```sql
create table if not exists competency (
  toadie text not null,
  task_type text not null,        -- e.g. 'pytest', 'webpack', 'generic'
  ema real not null default 0.0,  -- smoothed competency 0..1
  samples integer not null default 0,
  leash_level text not null default 'probation',  -- probation|spot_check|trusted
  updated_at text not null default current_timestamp,
  primary key (toadie, task_type)
);

create table if not exists grades (
  id text primary key,
  toadie text not null,
  task_type text not null,
  score real not null,            -- 0..1
  source text not null,           -- 'rubric' | 'outcome'
  prompt_hash text,
  output_hash text,
  event_id text references events(id) on delete set null,
  created_at text not null default current_timestamp
);
```

Competency is keyed **per Toadie × per task_type**: being good at `pytest` output implies little
about `webpack` output, so the two carry independent scores and leash levels.

## Scoring mechanics

### EMA update

```
ema = α·score + (1-α)·ema      # α ≈ 0.30, adjustable
samples += 1
```

The very first grade for a (Toadie, task_type) **seeds** the EMA to that score rather than
blending from 0.0, so a competent Toadie isn't dragged down by a cold start. Both rubric and
outcome grades update the same EMA. Defaults are config-tunable.

### Leash transitions (slow to earn, fast to lose)

```
PROMOTE   probation  → spot_check : ema ≥ 0.85 AND samples ≥ 8
          spot_check → trusted    : ema ≥ 0.92 AND samples ≥ 20
DEMOTE    trusted    → spot_check : ema < 0.80      (hysteresis gap vs 0.92 prevents flapping)
          spot_check → probation  : ema < 0.65
INSTANT   any audit scoring < 0.30 → drop exactly one level immediately, regardless of EMA
```

The minimum-sample gates prevent promotion on a lucky short streak. The instant-demote catches
regressions fast — trust takes ~dozens of good grades to build and one bad audit to dent.

### Audit-sampling gate (the review rate)

`should_audit(level, rng)` returns whether a given output gets a paid review:

```
probation : p = 1.00   (always — the investment phase)
spot_check : p = 0.20  (~1 in 5)
trusted    : p = 0.05  (~1 in 20)
```

The RNG is injected so the ratios are testable deterministically.

### Per-output confidence override

The leash level sets the *default* review rate, but a single output can override it **upward**.
`needs_review(level, rng, *, confidence, truncated)` returns True (force paid review) when:

- the output was **truncated** (`finish_reason == 'length'`) — an incomplete answer is suspect; or
- a **per-output confidence** is provided and falls below `CONFIDENCE_FLOOR` (≈ 0.55, tunable).

Otherwise it falls back to `should_audit(level, rng)`. Confidence is optional: when absent
(`None`), `needs_review` behaves exactly like `should_audit`. The signal is derived from the
**local** model's token logprobs (mean token probability / inverse entropy), never from the
model's self-described confidence. The override only ever *raises* review, never lowers it, so a
bad confidence estimate can cost a little extra review but can never wrongly grant trust.

### Outcome signals

Recorded as grades with `source='outcome'`:

- Agent used the summary and never requested the raw artifact → merit (~0.90).
- Agent re-requested the raw artifact, or re-ran the task → demerit (~0.20).

### J-curve accounting

The Accountant maintains, per Toadie × task_type, two running tallies:

- **invested** — review tokens spent grading this Toadie (cost incurred to build trust).
- **avoided** — estimated review tokens *not* spent because trusted output was used directly.

`toadiectl accountant status` renders the curve honestly, e.g.:

```
gremlin/pytest   TRUSTED    ema 0.94  (n=37)   invested ~12k tok · saved ~41k · net +29k
gremlin/webpack  PROBATION  ema 0.71  (n=5)    invested ~6k tok · saved 0 · (earning trust)
```

A "review" is estimated to cost on the order of the reviewed output's size in tokens; the
estimate is intentionally rough and labeled as such (consistent with SPEC-001's stance that
budget figures are estimates, not exact provider accounting). When the host exposes **real
token-usage counts** in its response metadata, prefer those over the char-based estimate.

## Error handling & safety

- **Fail open on sidecar trouble:** if the trust DB is unreachable or a grade can't be recorded,
  the Toadie still returns its output and the system defaults to the *most cautious* leash
  (`probation` → recommend review). A broken loop never blocks work and never silently grants
  trust.
- **Trust defaults low:** unknown (Toadie, task_type) pairs start at `probation`.
- **Read-only invariant:** nothing in this loop grants any Toadie write/exec capability.
- **Grades are append-only** (a full audit trail). The `competency` row holds the running EMA,
  updated incrementally per grade; it can be rebuilt by replaying `grades` if ever needed.

## Testing

Entirely deterministic and model-free, via TDD (same discipline as Gremlin):

- EMA tracks a simulated stream of grades within tolerance.
- Promotion fires only when *both* the EMA threshold and the min-sample gate are met; never on a
  short lucky streak.
- A single sub-0.30 audit demotes exactly one level immediately.
- Hysteresis: a Toadie hovering at ema≈0.83 does not flap between levels.
- `should_audit` produces the expected ratios under a seeded RNG.
- `needs_review` forces review on low confidence or truncation even for a Trusted Toadie, and
  falls back to `should_audit` when confidence is absent.
- Fail-open: with the store unavailable, calls still succeed and report `probation`.

## Scope

**This spec covers** the deterministic trust engine, the two tables, the Accountant surfacing,
and the dataset logger.

**Out of scope here (separate specs/plans):** the MCP `submit_grade` tool and `review` hints
(needs the MCP server, not yet built), the actual paid-AI rubric prompt wording, Bouncer/Scout,
and any fine-tuning.

## Open questions (non-blocking; sensible defaults chosen)

- Exact α and threshold constants will likely need tuning against real usage; they live in config.
- `task_type` classification (how a raw log is bucketed into `pytest`/`webpack`/`generic`) starts
  as a simple heuristic in Gremlin and can be refined later.
