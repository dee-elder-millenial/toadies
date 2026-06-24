import pytest

from toadies import dispatch, localai, registry

WB = "https://dees-workbench.local/"
DT = "http://dees-desktop.local:11434/"

CONFIG = f'''
[boxes.workbench]
url = "{WB}"

[boxes.desktop]
url = "{DT}"

[routing]
fallback = "workbench"

[toadies.gremlin]
tier = "deterministic"
handler = "gremlin_compress"

[toadies.scribe]
tier = "cpu-model"
box = "desktop"
model = "llama3.2:3b"
timeout_s = 30
'''


def _reg(tmp_path):
    p = tmp_path / "toady_registry.toml"
    p.write_text(CONFIG)
    return registry.load(str(p))


def _ok_transport(text):
    def transport(url, payload, timeout, headers):
        transport.calls.append((url, payload["model"], timeout))
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {}}
    transport.calls = []
    return transport


def test_dispatch_deterministic_calls_the_tools_handler(tmp_path):
    reg = _reg(tmp_path)
    raw = "\n".join(f"log line {i}" for i in range(50))
    raw += "\nFAILED tests/test_x.py::t - assert 1 == 2\n"
    out = dispatch.dispatch(reg, "gremlin", {"text": raw})
    assert out["ok"] is True
    assert out["summary_chars"] < out["original_chars"]


def test_dispatch_model_toady_calls_its_box_url_with_pinned_model(tmp_path):
    reg = _reg(tmp_path)
    transport = _ok_transport("a tidy summary")
    out = dispatch.dispatch(
        reg, "scribe", {"messages": [{"role": "user", "content": "summarize"}]},
        transport=transport,
    )
    assert out["ok"] is True
    assert out["text"] == "a tidy summary"
    assert out["box"] == "desktop"
    assert out["fell_back"] is False
    assert transport.calls == [("http://dees-desktop.local:11434/v1/chat/completions", "llama3.2:3b", 30)]


def test_dispatch_falls_back_to_gpu_when_box_is_down(tmp_path):
    reg = _reg(tmp_path)
    dt_url = DT.rstrip("/") + "/v1/chat/completions"

    def transport(url, payload, timeout, headers):
        if url == dt_url:
            raise ConnectionError("desktop down")
        return {"choices": [{"message": {"content": "fallback summary"}, "finish_reason": "stop"}], "usage": {}}

    out = dispatch.dispatch(
        reg, "scribe", {"messages": [{"role": "user", "content": "x"}]}, transport=transport,
    )
    assert out["ok"] is True
    assert out["text"] == "fallback summary"
    assert out["box"] == "workbench"        # ran on the fallback GPU box
    assert out["fell_back"] is True


def test_dispatch_raises_toady_unavailable_when_both_boxes_fail(tmp_path):
    reg = _reg(tmp_path)

    def transport(url, payload, timeout, headers):
        raise ConnectionError("everything is down")

    with pytest.raises(dispatch.ToadyUnavailable) as excinfo:
        dispatch.dispatch(
            reg, "scribe", {"messages": [{"role": "user", "content": "x"}]}, transport=transport,
        )
    assert excinfo.value.tried == ["desktop", "workbench"]


def test_run_routes_through_the_default_registry(tmp_path, monkeypatch):
    cfg = tmp_path / "toady_registry.toml"
    cfg.write_text(CONFIG)
    monkeypatch.setenv("TOADIES_REGISTRY", str(cfg))
    raw = "log line\n" * 30 + "FAILED tests/test_x.py::t - assert 1 == 2\n"
    out = dispatch.run("gremlin", {"text": raw})
    assert out["ok"] is True
    assert out["summary_chars"] < out["original_chars"]
