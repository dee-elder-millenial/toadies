import pytest

from toadies import tools


def test_list_tools_exposes_the_built_toadies():
    defs = tools.list_tools()
    names = {t["name"] for t in defs}
    assert {"gremlin_compress", "bouncer_scan", "accountant_status", "submit_grade"} <= names
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
