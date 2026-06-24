import pytest
from toadies import tools


class FailingStore:
    def __init__(self, *args, **kwargs):
        raise OSError("trust store unavailable")


def test_list_tools_exposes_the_built_toadies():
    defs = tools.list_tools()
    names = {t["name"] for t in defs}
    assert {"gremlin_compress", "bouncer_scan", "accountant_status", "submit_grade"} <= names
    assert "judge_and_grade" in names
    assert "toadie_interject" in names
    assert "toadie_interjection_inbox" in names
    # every tool advertises an MCP-shaped schema
    for t in defs:
        assert t["name"] and t["description"]
        assert t["inputSchema"]["type"] == "object"


def test_dispatch_gremlin_compress_reduces_and_keeps_signal():
    raw = "\n".join(f"tests/test_{i}.py .... [{i}%]" for i in range(200))
    raw += "\nE   assert 200 == 401\nFAILED tests/test_auth.py::test_x - assert 200 == 401\n"
    out = tools.dispatch("gremlin_compress", {"text": raw})
    assert out["ok"] is True
    assert out["summary_chars"] < out["original_chars"]
    assert "200 == 401" in out["summary_markdown"]


def test_dispatch_bouncer_scan_blocks_secret():
    out = tools.dispatch("bouncer_scan", {"text": "-----BEGIN RSA PRIVATE KEY-----"})
    assert out["decision"] == "block"
    assert out["safe"] is False


def test_dispatch_submit_grade_then_accountant_status(tmp_path):
    db = str(tmp_path / "t.db")
    graded = tools.dispatch("submit_grade",
                            {"toadie": "gremlin", "task_type": "pytest", "score": 0.9}, db_path=db)
    assert graded["leash_level"] == "probation"

    status = tools.dispatch("accountant_status", {}, db_path=db)
    keys = {(r["toadie"], r["task_type"]) for r in status["rows"]}
    assert ("gremlin", "pytest") in keys


def test_dispatch_unknown_tool_raises():
    with pytest.raises(tools.ToolError):
        tools.dispatch("nope", {})


def test_submit_grade_rejects_invalid_score_or_source():
    with pytest.raises(tools.ToolError):
        tools.dispatch("submit_grade", {"toadie": "gremlin", "task_type": "pytest", "score": 1.2}, db_path=None)
    with pytest.raises(tools.ToolError):
        tools.dispatch("submit_grade", {"toadie": "gremlin", "task_type": "pytest", "score": 0.5, "source": "bad"}, db_path=None)


def test_dispatch_accountant_status_fails_open_when_db_is_down(monkeypatch):
    monkeypatch.setattr(tools, "Store", FailingStore)

    out = tools.dispatch("accountant_status", {}, db_path="/tmp/no_db")
    assert out["ok"] is False
    assert out["rows"] == []
    assert "unavailable" in out["summary_markdown"].lower()


def test_submit_grade_fails_open_on_db_error_without_crash(monkeypatch):
    monkeypatch.setattr(tools, "Store", FailingStore)

    out = tools.dispatch("submit_grade",
                         {"toadie": "gremlin", "task_type": "pytest", "score": 0.9}, db_path="/tmp/no_db")
    assert out["ok"] is False
    assert out["toadie"] == "accountant"
    assert out["leash_level"] == "probation"
    assert out["ema"] == 0.0
    assert out["samples"] == 0
    assert "error" in out


def test_dispatch_toadie_interject_delegates_to_interjection_module(monkeypatch):
    calls = {}

    def fake_post_interjection(toadie, task_type, **kwargs):
        calls["toadie"] = toadie
        calls["task_type"] = task_type
        calls["kwargs"] = kwargs
        return {
            "ok": True,
            "delivery": "append",
            "toadie": toadie,
            "task_type": task_type,
            "event_id": "abc123",
            "trust": {"leash_level": "trusted", "ema": 0.95, "samples": 30},
        }

    monkeypatch.setattr(tools.interjection, "post_interjection", fake_post_interjection)

    out = tools.dispatch(
        "toadie_interject",
        {
            "toadie": "toadette",
            "task_type": "pytest",
            "message": "flagged suspicious pattern",
            "urgency": "high",
            "requested_delivery": "interrupt",
            "task_context": {"file": "x.py"},
        },
        db_path="/tmp/db",
    )

    assert out["ok"] is True
    assert out["delivery"] == "append"
    assert calls["toadie"] == "toadette"
    assert calls["task_type"] == "pytest"
    assert calls["kwargs"]["urgency"] == "high"
    assert calls["kwargs"]["requested_delivery"] == "interrupt"


def test_dispatch_toadie_interjection_inbox_queries_interjection_module(monkeypatch):
    def fake_list_interjections(**kwargs):
        return [
            {
                "event_id": "evt-1",
                "toadie": "toadette",
                "task_type": "pytest",
                "delivery": "interrupt",
                "urgency": "critical",
                "message": "token leak found",
                "created_at": "2026-06-23T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr(tools.interjection, "list_interjections", fake_list_interjections)

    out = tools.dispatch(
        "toadie_interjection_inbox",
        {
            "toadie": "toadette",
            "urgency": "critical",
            "limit": 5,
        },
        db_path="/tmp/db",
    )

    assert out["ok"] is True
    assert len(out["interjections"]) == 1
    assert out["interjections"][0]["event_id"] == "evt-1"


def test_dispatch_judge_and_grade_calls_reviewer_and_returns_output(monkeypatch):
    def fake_review_and_record(toadie, task_type, input_text, output_text, **kwargs):
        return {
            "ok": True,
            "toadie": toadie,
            "task_type": task_type,
            "score": 0.91,
            "leash_level": "probation",
            "ema": 0.91,
            "samples": 1,
        }

    calls = {}

    def fake_review_and_record_with_capture(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return fake_review_and_record(*args, **kwargs)

    monkeypatch.setattr(tools.reviewer, "review_and_record", fake_review_and_record_with_capture)

    out = tools.dispatch("judge_and_grade", {
        "toadie": "gremlin",
        "task_type": "pytest",
        "input_text": "input",
        "output_text": "output",
        "source": "rubric",
    }, db_path="/tmp/db")

    assert out["ok"] is True
    assert out["score"] == 0.91
    assert calls["args"][0] == "gremlin"
    assert calls["args"][1] == "pytest"
    assert calls["args"][2] == "input"
    assert calls["args"][3] == "output"
    assert calls["kwargs"]["db_path"] == "/tmp/db"
    assert calls["kwargs"]["source"] == "rubric"


def test_dispatch_judge_and_grade_requires_output_text():
    with pytest.raises(tools.ToolError):
        tools.dispatch("judge_and_grade", {"toadie": "gremlin", "task_type": "pytest"})


def test_dispatch_judge_and_grade_rejects_invalid_source(monkeypatch):
    with pytest.raises(tools.ToolError):
        tools.dispatch("judge_and_grade", {
            "toadie": "gremlin", "task_type": "pytest", "output_text": "out", "source": "bad"
        })
