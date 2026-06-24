"""LocalAI client — thin OpenAI-compatible chat wrapper.

The request-building and response-parsing are separated from the HTTP transport (which
is injectable) so the logic is testable without a live server. Toadies call this only as
an *optional enhancement*; callers must fail open if LocalAI is down (catch LocalAIError).
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("LOCALAI_BASE_URL", "https://127.0.0.1:8443")


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
    context = _http_ssl_context(url)
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return json.loads(resp.read().decode())


def _http_ssl_context(url: str):
    if not url.lower().startswith("https://"):
        return None

    cafile = os.environ.get("LOCALAI_CA_BUNDLE")
    if not cafile:
        default_local_cert = Path(__file__).resolve().parent.parent / "localai" / "caddy" / "certs" / "localai.crt"
        if default_local_cert.exists():
            cafile = str(default_local_cert)
    if not cafile:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=cafile)


def chat(messages, *, model, base_url=DEFAULT_BASE_URL, timeout=60,
         api_key=None, transport=None, max_tokens=None, temperature=None) -> ChatResult:
    transport = transport or _http_transport
    if api_key is None:
        api_key = os.environ.get("LOCALAI_API_KEY")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
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
