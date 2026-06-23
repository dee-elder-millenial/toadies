import pytest

from toadies import localai


def _fake_openai_response():
    return {
        "choices": [
            {"message": {"role": "assistant", "content": "3 tests failed around token expiry."},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132},
    }


def test_chat_parses_content_usage_and_finish_reason():
    captured = {}

    def fake_transport(url, payload, timeout, headers):
        captured["url"] = url
        captured["payload"] = payload
        return _fake_openai_response()

    result = localai.chat(
        [{"role": "user", "content": "summarize"}],
        model="llama-3.2-1b-instruct",
        transport=fake_transport,
    )

    assert result.ok is True
    assert result.text == "3 tests failed around token expiry."
    assert result.finish_reason == "stop"
    assert result.usage["total_tokens"] == 132
    # it posts to the OpenAI-compatible endpoint with the model + messages
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["payload"]["model"] == "llama-3.2-1b-instruct"
    assert captured["payload"]["messages"][0]["content"] == "summarize"


def test_chat_raises_localai_error_on_transport_failure():
    def broken_transport(url, payload, timeout, headers):
        raise OSError("connection refused")

    with pytest.raises(localai.LocalAIError):
        localai.chat([{"role": "user", "content": "x"}],
                     model="m", transport=broken_transport)


def test_chat_sends_bearer_header_when_api_key_set():
    captured = {}

    def fake_transport(url, payload, timeout, headers):
        captured["headers"] = headers
        return _fake_openai_response()

    localai.chat([{"role": "user", "content": "x"}], model="m",
                 api_key="secret123", transport=fake_transport)
    assert captured["headers"]["Authorization"] == "Bearer secret123"


def test_chat_reads_api_key_from_env_when_not_passed(monkeypatch):
    monkeypatch.setenv("LOCALAI_API_KEY", "envkey")
    captured = {}

    def fake_transport(url, payload, timeout, headers):
        captured["headers"] = headers
        return _fake_openai_response()

    localai.chat([{"role": "user", "content": "x"}], model="m", transport=fake_transport)
    assert captured["headers"]["Authorization"] == "Bearer envkey"


def test_chat_sends_no_auth_header_without_key(monkeypatch):
    monkeypatch.delenv("LOCALAI_API_KEY", raising=False)
    captured = {}

    def fake_transport(url, payload, timeout, headers):
        captured["headers"] = headers
        return _fake_openai_response()

    localai.chat([{"role": "user", "content": "x"}], model="m", transport=fake_transport)
    assert "Authorization" not in captured["headers"]
