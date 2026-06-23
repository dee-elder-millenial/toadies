"""LocalAI client — thin OpenAI-compatible chat wrapper.

The request-building and response-parsing are separated from the HTTP transport (which
is injectable) so the logic is testable without a live server. Toadies call this only as
an *optional enhancement*; callers must fail open if LocalAI is down (catch LocalAIError).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://127.0.0.1:8080"


class LocalAIError(Exception):
    """Raised when the local model call fails (down, timeout, bad response)."""


@dataclass
class ChatResult:
    text: str
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    ok: bool = True


def _http_transport(url, payload, timeout, headers):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def chat(messages, *, model, base_url=DEFAULT_BASE_URL, timeout=60,
         api_key=None, transport=None) -> ChatResult:
    transport = transport or _http_transport
    if api_key is None:
        api_key = os.environ.get("LOCALAI_API_KEY")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages}
    try:
        raw = transport(base_url.rstrip("/") + "/v1/chat/completions", payload, timeout, headers)
        choice = raw["choices"][0]
        return ChatResult(
            text=choice["message"]["content"],
            finish_reason=choice.get("finish_reason"),
            usage=raw.get("usage", {}),
        )
    except LocalAIError:
        raise
    except Exception as exc:  # transport, JSON, or shape error
        raise LocalAIError(str(exc)) from exc
