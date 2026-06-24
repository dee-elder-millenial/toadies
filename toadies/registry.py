"""Toady registry — static routing config for the distribution layer.

Loads `toady_registry.toml` and resolves a toady name to a Route describing how the
dispatcher should invoke it (in-process for deterministic toadies, or over HTTP to an
Ollama box for model-backed ones). Pure and inspectable; no network here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

VALID_TIERS = ("deterministic", "cpu-model", "gpu-model")
MODEL_TIERS = ("cpu-model", "gpu-model")


class RegistryError(Exception):
    """Invalid registry config, or a request for an unknown toady."""


@dataclass(frozen=True)
class Route:
    name: str
    tier: str
    handler: str | None = None
    box: str | None = None
    url: str | None = None
    model: str | None = None
    timeout_s: int = 60


class Registry:
    def __init__(self, boxes: dict, routing: dict, toadies: dict):
        self._boxes = boxes
        self._routing = routing
        self._toadies = toadies

    def fallback_box(self) -> str:
        return self._routing["fallback"]

    def box_url(self, box: str) -> str:
        return self._boxes[box]["url"]

    def resolve(self, name: str) -> Route:
        if name not in self._toadies:
            raise RegistryError(f"unknown toady: {name!r}")
        spec = self._toadies[name]
        tier = spec["tier"]
        if tier == "deterministic":
            return Route(name=name, tier=tier, handler=spec.get("handler"))
        box = spec.get("box")
        return Route(
            name=name,
            tier=tier,
            box=box,
            url=self._boxes.get(box, {}).get("url"),
            model=spec.get("model"),
            timeout_s=spec.get("timeout_s", 60),
        )


def load(path: str) -> Registry:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    boxes = data.get("boxes", {})
    routing = data.get("routing", {})
    toadies = data.get("toadies", {})
    _validate(boxes, routing, toadies)
    return Registry(boxes=boxes, routing=routing, toadies=toadies)


def _validate(boxes: dict, routing: dict, toadies: dict) -> None:
    fallback = routing.get("fallback")
    if fallback is not None and fallback not in boxes:
        raise RegistryError(f"routing.fallback names unknown box: {fallback!r}")
    for name, spec in toadies.items():
        tier = spec.get("tier")
        if tier not in VALID_TIERS:
            raise RegistryError(f"toady {name!r}: invalid tier {tier!r}")
        if tier in MODEL_TIERS:
            box = spec.get("box")
            if box not in boxes:
                raise RegistryError(f"toady {name!r}: unknown box {box!r}")
            if not spec.get("model"):
                raise RegistryError(f"toady {name!r}: missing model")
