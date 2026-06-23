import json

from toadies.cli import main


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
