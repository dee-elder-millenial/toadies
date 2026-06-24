# Bouncer v2 — Advisory Entropy, Benign-Shape Filter, Tunable Config

**Date:** 2026-06-24
**Status:** Approved design; ready for implementation plan.
**Supersedes behavior in:** `toadies/bouncer.py` (deterministic secret guard).

## 1. Context & Goal

Bouncer is the deterministic secret/safety guard (regex patterns + Shannon-entropy
heuristic). The **pattern matches** (private key, `AKIA…`, `sk-…`, `gh[pousr]_…`, AWS) are
high-precision and have produced **zero** false positives. The **entropy heuristic**
(any token ≥20 chars with letters+digits and entropy ≥3.5 bits) fires on ordinary
structured strings — file paths in diff headers, date-slugs, identifiers — and because
*any* finding currently `block`s, it has blocked every commit with a false alarm.

**Goal:** keep Bouncer's hard guarantee (never let a real credential through) while making
its entropy lane useful instead of obstructive — and make every threshold/rule/policy
**tunable via config (no code edits)**, adjustable by Dee, Toadette, and Robot.

## 2. Locked Decisions

1. **Two-tier verdict.** Pattern matches → `block`. Entropy hits → `warn` (non-blocking,
   `safe=True`). Never let the fuzzy heuristic gate a commit.
2. **Benign-shape filter (second pass).** Before an entropy candidate is reported, run it
   through a growable list of recognizers; drop it if it matches a known-benign structure
   (paths, date-slugs, diff-header prefixes, UUIDs…). Structural, **base64-safe**.
3. **Entropy bits as a score.** Each surviving entropy finding carries its entropy value,
   so warnings are graded/skimmable.
4. **Tunable config, not hardcoded — split by stakes.** Knobs live in two files:
   `bouncer_advisory.toml` (thresholds, recognizers, warn policy) and `bouncer_gate.toml`
   (blocking patterns + `block_severities`). Code holds defaults as a fallback. The
   `bouncer_config` MCP tool — the agent-facing "API" (no HTTP server; Bouncer is in-process)
   — reads both but **writes only the advisory file**.
5. **Safety invariant (structural).** The benign filter and the agent-tunable knobs touch
   the **advisory lane only**; the gate file is **unreachable from the agent surface** (§7).
   No agent action can suppress a *pattern* match or weaken the hard block.

## 3. Architecture (scan flow)

```
scan(text, *, redact=False, config=None):
  cfg = config or load_config()              # file overrides baked-in defaults
  for each line:
    pattern lane:  cfg.patterns  -> Finding(kind, severity, score=None)   # BLOCKS
    entropy lane:  tokens -> _looks_like_secret(cfg) and not _matches_known
                          and not _is_benign(token, cfg)                   # WARNS
                   -> Finding(kind="high_entropy", severity="medium",
                              score=<entropy bits>)
  decision:
    any finding.severity in cfg.block_severities -> ("block", safe=False)
    elif any entropy finding                     -> ("warn",  safe=True)
    else                                         -> ("allow", safe=True)
```

`ScanResult` gains nothing required beyond the existing fields; `Finding` gains
`score: float | None`. An optional `suppressed: list[(token, recognizer)]` is returned
when `debug=True`, so the filter is self-documenting for tuning.

### Decision/CLI semantics
- `block` → CLI/commit hook exits non-zero (unchanged hard stop).
- `warn` → exits **zero**, prints advisories (with scores). Commits proceed.
- `allow` → exits zero, silent.

## 4. The Benign-Shape Filter

`_is_benign(token, cfg) -> (bool, recognizer_name | None)`. Each recognizer is named so a
match is traceable. Two kinds:

- **Regex recognizers** — fully defined in config; add new ones with no code:
  - `diff_header_path` — leading `a/…` or `b/…` git-diff path.
  - `date_slug` — contains `\d{4}-\d{2}-\d{2}` (dates / dated slugs).
  - `uuid` — `8-4-4-4-12` hex.
- **Structural recognizers** — code-backed (toggle + params in config):
  - `filesystem_path` — token splits on `/` into ≥2 segments where **each segment is
    itself low-entropy / word-like** (NOT base64). This is the base64-safe check: a real
    path has word segments; a base64 blob is one high-entropy run that happens to contain
    `/`, so its "segments" are still high-entropy and it is **not** suppressed.

The list is ordered and short; it runs only against already-flagged tokens, so the cost is
negligible. New false-positive shapes are added by appending a recognizer (regex in config,
or a small code predicate for structural ones).

## 5. Config — two files, split by stakes

Both are source-of-truth; an absent file ⇒ baked-in defaults. Illustrative:

**`bouncer_advisory.toml`** (agent-tunable):
```toml
[entropy]
min_len = 20
bits    = 3.5

[policy]
warn_severities = ["medium"]            # which non-gate severities surface as warnings

[benign]
enabled = ["diff_header_path", "filesystem_path", "date_slug", "uuid"]
# add your own pure-regex recognizer without touching code:
[[benign.custom]]
name  = "k8s_resource_path"
regex = "^/(api|apis)/.*"
```

**`bouncer_gate.toml`** (strong-tier only; the agent tool will not write this):
```toml
block_severities = ["critical", "high"]

[[patterns]]
kind = "private_key"
severity = "critical"
regex = "-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
# … AKIA, sk-, gh_, AWS_SECRET …
```

`load_config(advisory_path=None, gate_path=None)` reads `$BOUNCER_ADVISORY` /
`$BOUNCER_GATE` or the repo defaults, merges each over baked-in defaults, validates (known
severities; compilable regexes), and returns a `BouncerConfig` exposing both — but only the
advisory half is writable through the API in §6.

## 6. The `bouncer_config` MCP tool (the "API")

- `bouncer_config` with `{"action": "get"}` → returns the **effective** config (advisory +
  gate, read-only view) as JSON.
- `bouncer_config` with `{"action": "set", "patch": {...}}` → merges the patch into
  **`bouncer_advisory.toml` only**, re-validates, writes it, returns the new effective
  config. A patch that targets gate keys (`patterns`, `block_severities`) is **rejected** —
  the gate is not writable through this tool (§7). Changing the gate is a strong-tier action
  done by editing `bouncer_gate.toml` directly / via a privileged CLI.

Every accepted change is a visible, revertible diff in git.

## 7. Safety, Trust & the Training Relationship (IN SCOPE — the core of this design)

**Principle:** the local agents (Toadette + toadies) are weak and *in training*. The
**strong tier — Dee, Robot, Claude — are the trainers and the guardians of anything that
can fail dangerously.** Bouncer's hard gate is exactly such a thing. So the gate is
protected **by construction, not by honor system.** Two configs, two levels of reach:

- **`bouncer_advisory.toml`** — entropy thresholds, benign recognizers, warn policy.
  **Freely tunable by Toadette/toadies** via the `bouncer_config` MCP tool. Low stakes:
  the worst an agent can do here is make a *warning* noisier or quieter.
- **`bouncer_gate.toml`** — the blocking patterns + `block_severities` (the actual
  credential-stoppers). **The agent-facing `bouncer_config` tool CANNOT write this file.**
  It is changed only by the strong tier: Dee directly, or Robot/Claude via a privileged
  CLI path the in-training agents do not have. Toadette literally has no tool that can
  weaken the gate.

**Why structural, not policy:** because the agent surface can't reach the gate file, and
the benign filter only runs in the advisory lane (§2.5), no amount of agent tuning — bug,
drift, or a confidently-wrong local model — can let a real credential through. Fail-safe by
design, which is the whole Bouncer ethos (deterministic, not "trust me").

**Where the training pays off:** advisory tuning is exactly the judgment the local agents
*learn*. A proposed benign recognizer ("suppress this path shape") is a **gradeable action**
— the strong judge verifies it doesn't suppress real secrets before it's accepted, and the
trust-loop's leash sets how much review each proposal needs. So Bouncer gets *better* over
time precisely because the strong AIs train the tuning. That pipeline (strong-graded
advisory-config proposals) is the natural slice right after v2.

## 8. Testing (TDD)

1. Pattern match (e.g. `BEGIN PRIVATE KEY`) → `block`, `safe=False` (unchanged).
2. A bare high-entropy token (no benign shape) → `warn`, `safe=True`, finding has a `score`.
3. **Benign filter:** `a/docs/.../2026-06-23-…-design` path → **dropped** (no finding).
4. **Base64 safety:** a fake base64-looking secret blob → **survives** the path filter →
   `warn` (not suppressed).
5. `date_slug` / `uuid` recognizers drop their shapes.
6. Decision logic: pattern + entropy together → `block` (pattern wins).
7. Config override: lower `entropy.bits` via config changes what’s flagged; `block_severities`
   change moves entropy to `block` when configured.
8. `load_config` validates (bad severity / uncompilable regex → error) and falls back to
   defaults when no file.
9. `bouncer_config` get returns effective config; set merges + persists + re-reads the
   advisory file.
10. **Gate is unreachable from the API:** `bouncer_config set` with a patch targeting
    `patterns` or `block_severities` is **rejected**, and `bouncer_gate.toml` is left
    untouched. (The structural safety invariant from §7 — arguably the most important test.)

## 9. Build Sequence

1. `BouncerConfig` + `load_config` (defaults + file merge + validation) — tests 7–8.
2. `Finding.score`; entropy lane emits bits — test 2.
3. Benign-shape filter (`_is_benign`, recognizers) — tests 3–5.
4. Two-tier decision logic (`block`/`warn`/`allow`) — tests 1, 6.
5. CLI/hook exit-code change (non-zero only on `block`).
6. `bouncer_config` MCP tool (get/set) — test 9.

## 10. Out of Scope / Follow-ups

- **Strong-graded advisory-config proposals** — route an agent's proposed recognizer/threshold
  change through the trust-loop (strong judge verifies it suppresses no real secret) before
  it's accepted. The natural next slice; §7 sets it up. *(Gate protection itself is now in
  scope — the structural two-file split, not a deferred guard.)*
- An LLM "second opinion" on entropy warnings (Bouncer's docstring already reserves this as
  a *secondary*, never the gate).
- Input hygiene (scan only added `+` lines / skip diff metadata) — complementary, but the
  benign filter + advisory tiering already remove the pain; do later if still noisy.
