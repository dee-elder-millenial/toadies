# Toadies Distribution Layer — Design

**Date:** 2026-06-23
**Status:** Built (TDD) on `feat/ollama-distribution-layer` — `registry.py`, `dispatch.py`,
seeded `toady_registry.toml`, `toady_dispatch` MCP tool; verified live end-to-end; 96 tests
green. One box. (`engine.py` rename + native-`/api` logprobs path still deferred.)
**Companion:** `docs/toadette-distribution-diagram.html` (interactive block diagram).

## 1. Context & Goal

Toadette ("The Manager") is an agentic orchestrator that delegates work to specialized
**toadies**. Toadies live in different places by what they need: pure-Python ones run
in-process; model-backed ones run on whichever machine/engine suits them. The
**distribution layer** is the thin mechanism that lets Toadette invoke any toady without
caring where it physically runs.

**Topology: one machine.** There is only **dees-workbench** (RX 580 GPU, Ollama via
Vulkan). An earlier "two-box federation" idea (a separate `dees-desktop` CPU box) was
based on a typo — that machine doesn't exist; the only other device is a Windows laptop
(SBE-Lenovo), used as a client, not a worker. And it isn't needed: **Ollama auto-shares
GPU/CPU on the one box** (it spills a too-big model's layers onto the CPU itself), so the
idle CPU is already used without a second instance. The distribution layer still supports
multiple boxes + fallback in code (future-proof — e.g. if the laptop ever becomes an
occasional worker), but the seeded config is a single box.

**Engine:** Ollama (native install, GPU via Vulkan). Toadies reach it through its
OpenAI-compatible `/v1` endpoint; the confidence path uses the native `/api/chat`
endpoint (logprobs are not exposed on `/v1`).

**In scope:** the registry, the dispatcher, the placement tiers, fallback handling (kept
in code for the multi-box future, degenerate with one box), and the engine client.
**Out of scope (separate specs):** Toadette's planning/brain and the Caddy API-key gate.

## 2. Architecture

One brain, a registry, a dispatcher, tiers, one box (multi-box-ready).

- **Toadette** runs on the workbench. When she decides a toady should do a job, she hands
  `(toady_name, payload)` to the **dispatcher**.
- **Dispatcher** (`dispatch.py`) is the whole distribution layer: it resolves the toady
  via the registry and invokes it — either in-process (deterministic) or over HTTP to an
  Ollama endpoint (model-backed).
- **Registry** (`registry.py` + `toady_registry.toml`) is static, inspectable config.

| Tier | Invocation | Runs on |
|------|------------|---------|
| `deterministic` | in-process Python call via `tools.py` | wherever Toadette is — no box, no network |
| `gpu-model` | OpenAI `/v1` (or native `/api` for logprobs) | the workbench Ollama (GPU + automatic CPU overflow) |
| `cpu-model` | same client, a different box URL | *reserved for a future second box; unused today* |

Today every model toady is `gpu-model` on the one box; `cpu-model` exists only so a future
box can be added by editing the registry — no code change.

## 3. The Registry

A single static TOML file. **Boxes** are defined once; **toadies** reference a box by
name. Each model-backed toady **pins its own model** (a decision locked during design:
explicit + makes the GPU-fallback check trivial, and trivial to author given Ollama tags).

```toml
[boxes.workbench]                       # the one box: GPU + automatic CPU overflow
url         = "https://dees-workbench.local/"
api_key_env = "TOADIES_WORKBENCH_KEY"   # optional; ignored if unset

[routing]
fallback = "workbench"                  # degenerate with one box; the hook for a future box

[toadies.gremlin]
tier    = "deterministic"
handler = "gremlin_compress"            # an existing tools.py function name

[toadies.bouncer]
tier    = "deterministic"
handler = "bouncer_scan"

[toadies.accountant]
tier    = "deterministic"
handler = "accountant_status"

[toadies.scribe]                        # example model-backed toady
tier      = "gpu-model"
box       = "workbench"
model     = "llama3.2:3b"               # an Ollama tag
timeout_s = 30
```

> A second box would be added later as `[boxes.<name>]` + `cpu-model` toadies pointing at
> it; the dispatcher already routes and falls back across boxes (§4).

### Validation (at load)
1. Every `tier` is one of `deterministic | cpu-model | gpu-model`.
2. Deterministic toadies name a `handler` that exists in `tools.py`.
3. Model toadies name a `box` that exists, and a non-empty `model`.
4. **Fallback safety:** every model toady's `model` must be loadable on the
   `routing.fallback` box, or fallback can't work. (Initially a documented invariant; a
   live preflight `GET {fallback}/v1/models` check is a follow-up.)

`registry.py` exposes `load(path) -> Registry` and `Registry.resolve(toady) -> Route`,
where `Route` is a small dataclass `(name, tier, handler?, box?, url?, model?, timeout_s)`.

## 4. The Dispatcher

`dispatch.py` — `dispatch(toady_name, payload) -> dict`:

1. `route = registry.resolve(toady_name)` (raises `ToolError` if unknown).
2. **deterministic** → call `tools.dispatch(route.handler, payload)` in-process; return.
3. **model-backed** → call the engine client with `route.url` + `route.model`:
   - on success → return result.
   - on failure (timeout / connection / bad response) → **fallback**: re-issue against
     `boxes[routing.fallback].url` with the same `model`.
     - on success → return result (optionally flagged `fell_back=True`).
     - on failure → raise `ToadyUnavailable(toady_name, tried=[box, fallback])`.

The dispatcher is transport-injectable like `tools.py`/`localai.py`, so every path is
unit-testable without a live server.

## 5. Failure Handling

- Policy (locked): **always fall back to GPU**, then **fail loud**. No per-toady policy
  field — one `routing.fallback` line.
- Fallback **re-runs** the job (doesn't skip it), so safety toadies like Bouncer never get
  silently dropped even under failure. (Bouncer is deterministic anyway → in-process, no
  box to fail.)
- Backstop: if the fallback box also fails, raise `ToadyUnavailable` — surfaced to
  Toadette as a structured error, never a silent success.

## 6. Confidence / logprobs

The trust-loop's per-output confidence-escalation axis needs token logprobs. Ollama
exposes `logprobs`/`top_logprobs` only on the **native `/api/chat`** endpoint, not `/v1`.
So `engine.py` provides two calls:
- `chat(...)` → OpenAI `/v1/chat/completions` (normal path).
- `chat_with_logprobs(...)` → native `/api/chat` with `logprobs` (confidence path).

Toadette's competency (EMA/leash) axis is unaffected and works regardless.

## 7. Engine Client

Generalize the existing `localai.py` → `engine.py` (keep a `localai` shim if convenient):
- Keep the injectable-transport + `ChatResult` design and fail-open `EngineError`
  (renamed from `LocalAIError`).
- `DEFAULT_BASE_URL` driven by env; the dispatcher passes per-box URLs explicitly.
- TLS: reuse the existing `localai/caddy/certs/localai.crt` CA-bundle logic for the
  workbench HTTPS endpoint.

## 8. Testing (TDD)

Write tests first, one behavior at a time:
1. Registry: parses valid TOML; rejects bad tier / missing handler / unknown box / empty
   model (validation cases).
2. `resolve()` returns the right `Route` per tier.
3. Dispatch deterministic → calls the named `tools.py` handler, returns its result.
4. Dispatch model-backed (injected transport) → calls the box URL with the pinned model.
5. Fallback: primary transport raises → fallback transport is called with the same model.
6. Both fail → `ToadyUnavailable` raised with `tried=[...]`.
7. Confidence path: `chat_with_logprobs` hits native `/api/chat` and surfaces logprobs.

## 9. Build Sequence

1. `registry.py` + schema validation (tests 1–2).
2. `engine.py` (rename/generalize `localai.py`; tests for both transports).
3. `dispatch.py` (tests 3–6).
4. `chat_with_logprobs` confidence path (test 7).
5. Seed `toady_registry.toml` with the built deterministic toadies + one model toady.
6. Wire dispatch into `tools.py`/MCP so Toadette can call it.

## 10. Parked / Follow-ups

- Toadette's planning brain (separate spec).
- A second box only if/when one exists (e.g. the SBE-Lenovo laptop as an occasional
  worker) — add `[boxes.<name>]` + `cpu-model` toadies; no code change needed.
- Caddy API-key gate (auth currently open on LAN — accepted risk for the playground).
- Live `/v1/models` preflight for fallback-model validation.
