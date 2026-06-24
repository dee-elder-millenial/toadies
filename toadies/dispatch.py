"""Dispatcher — the distribution layer.

Given a toady name + payload, resolve its Route via the registry and invoke it:
in-process for deterministic toadies, or over HTTP to an Ollama box for model-backed
ones (with always-fall-back-to-GPU on failure, then a loud ToadyUnavailable).
"""

from __future__ import annotations

from . import localai, tools


class ToadyUnavailable(Exception):
    """A model-backed toady could not be reached on its box or the fallback box."""

    def __init__(self, toady: str, tried: list[str]):
        self.toady = toady
        self.tried = tried
        super().__init__(f"toady {toady!r} unavailable; tried boxes {tried}")


def dispatch(reg, toady_name: str, payload: dict, *, transport=None) -> dict:
    route = reg.resolve(toady_name)
    if route.tier == "deterministic":
        return tools.dispatch(route.handler, payload)

    messages = payload["messages"]
    tried: list[str] = []

    try:
        result = localai.chat(
            messages, model=route.model, base_url=route.url,
            timeout=route.timeout_s, transport=transport,
        )
        return _model_ok(toady_name, route.box, result, fell_back=False)
    except localai.LocalAIError:
        tried.append(route.box)

    # Always fall back to the GPU box, re-running with the same model.
    fb_box = reg.fallback_box()
    try:
        result = localai.chat(
            messages, model=route.model, base_url=reg.box_url(fb_box),
            timeout=route.timeout_s, transport=transport,
        )
        return _model_ok(toady_name, fb_box, result, fell_back=True)
    except localai.LocalAIError:
        tried.append(fb_box)
        raise ToadyUnavailable(toady_name, tried)


def _model_ok(toady: str, box: str, result, *, fell_back: bool) -> dict:
    return {
        "ok": True,
        "toady": toady,
        "box": box,
        "text": result.text,
        "finish_reason": result.finish_reason,
        "usage": result.usage,
        "fell_back": fell_back,
    }
