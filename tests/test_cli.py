import json

from toadies.cli import main
from toadies import cli


def _write_log(tmp_path):
    """A realistic noisy run: lots of passing-dot noise around one real failure."""
    p = tmp_path / "test-output.log"
    noise = "\n".join(f"tests/test_module_{i}.py ............ [{i}%]" for i in range(200))
    p.write_text(
        noise
        + "\n=================================== FAILURES ===================================\n"
        + "E   assert 200 == 401\n"
        + "tests/test_auth.py:482: AssertionError\n"
        + "FAILED tests/test_auth.py::test_refresh_token_expiry - assert 200 == 401\n"
    )
    return p


def test_gremlin_on_a_file_prints_markdown_summary(tmp_path, capsys):
    p = _write_log(tmp_path)

    rc = main(["gremlin", str(p)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "# Gremlin summary" in out
    assert "200 == 401" in out


def test_gremlin_json_flag_emits_valid_structured_payload(tmp_path, capsys):
    p = _write_log(tmp_path)

    rc = main(["gremlin", str(p), "--json"])

    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["toadie"] == "gremlin"
    assert payload["original_chars"] >= payload["summary_chars"]
    assert "200 == 401" in payload["summary_markdown"]


def test_grade_command_then_accountant_status(tmp_path, capsys):
    db = str(tmp_path / "toadies.db")

    rc = main(["grade", "gremlin", "pytest", "0.9", "--db", db])
    assert rc == 0
    capsys.readouterr()  # clear

    rc = main(["accountant", "status", "--db", db])
    assert rc == 0
    out = capsys.readouterr().out
    assert "gremlin/pytest" in out
    assert "PROBATION" in out  # one grade isn't enough to leave probation


def test_bouncer_cli_blocks_secret_with_nonzero_exit(capsys):
    rc = main(["bouncer", "--text", "x\n-----BEGIN RSA PRIVATE KEY-----\ny", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "block"
    assert payload["safe"] is False
    assert rc == 1  # nonzero so hooks/pipelines can fail closed on secrets


def test_bouncer_cli_allows_clean_text(capsys):
    rc = main(["bouncer", "--text", "an ordinary commit message", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "allow"


def test_bouncer_cli_warns_on_entropy_with_zero_exit(capsys):
    rc = main(["bouncer", "--text", "DB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "warn"
    assert payload["safe"] is True
    assert rc == 0  # advisory: high-entropy does not block commits


def test_bouncer_cli_warn_prints_findings_not_allow(capsys):
    rc = main(["bouncer", "--text", "DB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "warn" in out
    assert "no secrets detected" not in out  # must not be mislabeled as a clean allow


class FailingStore:
    def __init__(self, *args, **kwargs):
        raise OSError("trust store unavailable")


def test_grade_command_rejects_invalid_score(monkeypatch, capsys):
    rc = main(["grade", "gremlin", "pytest", "2.0"])
    assert rc != 0
    assert "score must be in [0.0, 1.0]" in capsys.readouterr().out.lower()


def test_grade_command_falls_back_to_probation_when_db_fails(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Store", FailingStore)

    rc = main(["grade", "gremlin", "pytest", "0.9", "--db", "/dev/null"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "grade: trust store unavailable" in out
    assert "gremlin/pytest: PROBATION" in out


def test_accountant_status_falls_back_when_db_fails(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Store", FailingStore)

    rc = main(["accountant", "status", "--db", "/dev/null"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "accountant: trust store unavailable" in out
    assert "starts on probation" in out.lower()


def test_judge_command_runs_and_prints_summary(tmp_path, capsys, monkeypatch):
    output_file = tmp_path / "toadie-output.txt"
    output_file.write_text("compressed output")

    calls = {}

    def fake_review_and_record(toadie, task_type, input_text, output_text, **kwargs):
        calls["toadie"] = toadie
        calls["task_type"] = task_type
        calls["input_text"] = input_text
        calls["output_text"] = output_text
        calls["kwargs"] = kwargs
        return {
            "ok": True,
            "toadie": toadie,
            "task_type": task_type,
            "score": 0.93,
            "leash_level": "probation",
            "ema": 0.93,
            "samples": 1,
        }

    monkeypatch.setattr(cli.reviewer, "review_and_record", fake_review_and_record)
    rc = main(["judge", "gremlin", "pytest", str(output_file), "--input-file-text", "reference text"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "judge: gremlin/pytest: score 0.93" in out
    assert calls["toadie"] == "gremlin"
    assert calls["input_text"] == "reference text"
    assert calls["output_text"] == "compressed output"
    assert calls["kwargs"]["model"] == cli.reviewer.DEFAULT_JUDGE_MODEL


def test_judge_command_json_flag_uses_return_payload(tmp_path, capsys, monkeypatch):
    output_file = tmp_path / "toadie-output.txt"
    output_file.write_text("compressed output")

    def fake_review_and_record(toadie, task_type, input_text, output_text, **kwargs):
        return {
            "ok": True,
            "toadie": toadie,
            "task_type": task_type,
            "score": 0.73,
            "leash_level": "probation",
            "ema": 0.73,
            "samples": 2,
        }

    monkeypatch.setattr(cli.reviewer, "review_and_record", fake_review_and_record)
    rc = main(["judge", "bouncer", "generic", str(output_file), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["score"] == 0.73
    assert payload["task_type"] == "generic"


def test_judge_command_returns_error_code_on_failure(tmp_path, capsys, monkeypatch):
    output_file = tmp_path / "toadie-output.txt"
    output_file.write_text("compressed output")

    def boom(*_args, **_kwargs):
        raise RuntimeError("localai unavailable")

    monkeypatch.setattr(cli.reviewer, "review_and_record", boom)
    rc = main(["judge", "gremlin", "pytest", str(output_file)])
    out = capsys.readouterr().out

    assert rc == 2
    assert "judge error: localai unavailable" in out


def test_interjections_command_prints_structured_json(monkeypatch, capsys):
    rows = [
        {
            "event_id": "evt-1",
            "created_at": "2026-06-23T12:00:00+00:00",
            "toadie": "toadette",
            "task_type": "pytest",
            "delivery": "append",
            "urgency": "medium",
            "message": "token usage spike in logs",
        }
    ]

    monkeypatch.setattr(cli.interjection, "list_interjections", lambda *_, **__: rows)

    rc = main(["interjections", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert isinstance(payload, list)
    assert payload[0]["event_id"] == "evt-1"


def test_interjections_command_renders_pretty(monkeypatch, capsys):
    rows = [
        {
            "event_id": "evt-1",
            "created_at": "2026-06-23T12:00:00+00:00",
            "toadie": "toadette",
            "task_type": "pytest",
            "delivery": "append",
            "urgency": "medium",
            "message": "token usage spike in logs",
        }
    ]

    monkeypatch.setattr(cli.interjection, "list_interjections", lambda *_, **__: rows)

    rc = main(["interjections", "--toadie", "toadette"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "toadette/pytest: token usage spike in logs" in out
