# Toadies Distribution Layer — Design

**Date:** 2026-06-23
**Status:** Approved design (brainstorm complete); ready for implementation plan.
**Companion:** `docs/toadette-distribution-diagram.html` (interactive block diagram).

## 1. Context & Goal

Toadette ("The Manager") is an agentic orchestrator that delegates work to specialized
**toadies**. Toadies live in different places by what they need: pure-Python ones run
in-process; model-backed ones run on whichever machine/engine suits them. The
**distribution layer** is the thin mechanism that lets Toadette invoke any toady without
caring where it physically runs.

This is "Flavor 2" federation: spread work across **dees-workbench** (RX 580 GPU, via
Ollama+Vulkan) and **dees-desktop** (idle CPU, future Ollama instance), so the GPU stays
free for interactive work and grunt jobs go elsewhere.

**Engine:** Ollama (native install, GPU via Vulkan). Toadies reach it through its
OpenAI-compatible `/v1` endpoint; the confidence path uses the native `/api/chat`
endpoint (logprobs are not exposed on `/v1`).

**In scope:** the registry, the dispatcher, the three placement tiers, fallback handling,
and the engine client. **Out of scope (separate specs):** Toadette's planning/brain,
standing up the dees-desktop Ollama, and the Caddy API-key gate.

## 2. Architecture

One brain, a registry, a dispatcher, three tiers, two boxes.

- **Toadette** runs on the workbench. When she decides a toady should do a job, she hands
  `(toady_name, payload)` to the **dispatcher**.
- **Dispatcher** (`dispatch.py`) is the whole distribution layer: it resolves the toady
  via the registry and invokes it — either in-process (deterministic) or over HTTP to an
  Ollama endpoint (model-backed).
- **Registry** (`registry.py` + `toady_registry.toml`) is static, inspectable config.

| Tier | Invocation | Runs on |
|------|------------|---------|
| `deterministic` | in-process Python call via `tools.py` | wherever Toadette is — no box, no network |
| `cpu-model` | OpenAI `/v1` (or native `/api`) over LAN | dees-desktop Ollama |
| `gpu-model` | same client, different URL | dees-workbench Ollama |

Adding a box = adding lines to the registry; no code change.

## 3. The Registry

A single static TOML file. **Boxes** are defined once; **toadies** reference a box by
name. Each model-backed toady **pins its own model** (a decision locked during design:
explicit + makes the GPU-fallback check trivial, and trivial to author given Ollama tags).

```toml
[boxes.workbench]                       # GPU — Toadette's home + the fallback target
url         = "https://dees-workbench.local/"
api_key_env = "TOADIES_WORKBENCH_KEY"   # optional; ignored if unset

[boxes.desktop]                         # CPU — the offload box (future)
url         = "http://dees-desktop.local:11434/"
api_key_env = "TOADIES_DESKTOP_KEY"

[routing]
fallback = "workbench"                  # where any model-toady retries when its box is down

[toadies.gremlin]
tier    = "deterministic"
handler = "gremlin_compress"            # an existing tools.py function name

[toadies.bouncer]
tier    = "deterministic"
handler = "bouncer_scan"

[toadies.scribe]                        # example model-backed toady
tier      = "cpu-model"
box       = "desktop"
model     = "llama3.2:3b"               # an Ollama tag
timeout_s = 30
```

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
- Stand up dees-desktop Ollama instance + add `[boxes.desktop]` for real.
- Caddy API-key gate (auth currently open on LAN — accepted risk for the playground).
- Live `/v1/models` preflight for fallback-model validation.
