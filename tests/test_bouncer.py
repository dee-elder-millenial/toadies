import pytest

from toadies import bouncer


def test_load_config_returns_baked_in_defaults_when_no_files(tmp_path):
    cfg = bouncer.load_config(
        advisory_path=str(tmp_path / "missing.toml"),
        gate_path=str(tmp_path / "missing.toml"),
    )
    assert cfg.entropy_min_len == 20
    assert cfg.entropy_bits == 3.5
    assert cfg.block_severities == ("critical", "high")
    assert any(kind == "private_key" for kind, _sev, _pat in cfg.patterns)


def test_load_config_advisory_file_overrides_entropy(tmp_path):
    adv = tmp_path / "advisory.toml"
    adv.write_text("[entropy]\nbits = 4.5\nmin_len = 24\n")
    cfg = bouncer.load_config(advisory_path=str(adv), gate_path=str(tmp_path / "missing.toml"))
    assert cfg.entropy_bits == 4.5
    assert cfg.entropy_min_len == 24
    assert cfg.block_severities == ("critical", "high")  # gate untouched


def test_load_config_gate_file_overrides_block_severities(tmp_path):
    gate = tmp_path / "gate.toml"
    gate.write_text('block_severities = ["critical"]\n')
    cfg = bouncer.load_config(advisory_path=str(tmp_path / "missing.toml"), gate_path=str(gate))
    assert cfg.block_severities == ("critical",)


def test_entropy_finding_carries_its_bits_as_score():
    text = "DB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj"
    result = bouncer.scan(text)
    entropy_findings = [f for f in result.findings if f.kind == "high_entropy"]
    assert entropy_findings
    assert entropy_findings[0].score >= 3.5


def _entropy(result):
    return [f for f in result.findings if f.kind == "high_entropy"]


def test_benign_filter_drops_a_filesystem_path():
    # lowercase, multi-segment, digit-bearing path — flagged by entropy, dropped as benign
    text = "edited a/docs/specs/v2-distribution-layer-design-rev3-final today"
    assert not _entropy(bouncer.scan(text))


def test_benign_filter_keeps_a_base64_looking_secret():
    # one high-entropy run with mixed case + base64 chars — NOT a path; must survive
    text = "token: aGVsbG8Vd29ybGQ4OTc2NTQzMjFAYmNkZWZnK1ptOXZMMmJhcg"
    assert _entropy(bouncer.scan(text))


def test_benign_filter_drops_a_date_slug():
    assert not _entropy(bouncer.scan("ref 2026-06-23-toadies-distribution-layer-design"))


def test_benign_filter_drops_a_uuid():
    assert not _entropy(bouncer.scan("id 550e8400-e29b-41d4-a716-446655440000 done"))


def test_high_entropy_warns_and_does_not_block():
    text = "DB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj"
    result = bouncer.scan(text)
    assert result.decision == "warn"
    assert result.safe is True


def test_pattern_match_still_blocks_even_alongside_entropy():
    text = "-----BEGIN RSA PRIVATE KEY-----\nDB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj"
    result = bouncer.scan(text)
    assert result.decision == "block"
    assert result.safe is False


def test_config_view_exposes_effective_config(tmp_path):
    view = bouncer.config_view(
        advisory_path=str(tmp_path / "missing.toml"),
        gate_path=str(tmp_path / "missing.toml"),
    )
    assert view["entropy"]["bits"] == 3.5
    assert "private_key" in view["gate"]["patterns"]
    assert view["gate"]["block_severities"] == ["critical", "high"]


def test_set_advisory_merges_and_persists(tmp_path):
    adv = tmp_path / "advisory.toml"
    bouncer.set_advisory({"entropy": {"bits": 4.0, "min_len": 22}}, advisory_path=str(adv))
    view = bouncer.config_view(advisory_path=str(adv), gate_path=str(tmp_path / "missing.toml"))
    assert view["entropy"]["bits"] == 4.0
    assert view["entropy"]["min_len"] == 22


def test_set_advisory_defaults_to_repo_path_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BOUNCER_ADVISORY", raising=False)
    monkeypatch.setattr(bouncer, "DEFAULT_ADVISORY_PATH", str(tmp_path / "bouncer_advisory.toml"))
    bouncer.set_advisory({"entropy": {"bits": 4.2}})
    assert (tmp_path / "bouncer_advisory.toml").exists()


def test_set_advisory_rejects_gate_keys_and_writes_nothing(tmp_path):
    adv = tmp_path / "advisory.toml"
    with pytest.raises(bouncer.BouncerConfigError):
        bouncer.set_advisory({"block_severities": ["critical"]}, advisory_path=str(adv))
    with pytest.raises(bouncer.BouncerConfigError):
        bouncer.set_advisory({"patterns": []}, advisory_path=str(adv))
    assert not adv.exists()  # the gate is unreachable; nothing persisted on rejection


def test_detects_private_key_and_blocks():
    text = "here is my key\n-----BEGIN RSA PRIVATE KEY-----\nMIIEdummy\n-----END RSA PRIVATE KEY-----\n"
    result = bouncer.scan(text)

    assert result.safe is False
    assert result.decision == "block"
    kinds = {f.kind for f in result.findings}
    assert "private_key" in kinds
    assert any(f.severity == "critical" for f in result.findings)


def test_clean_text_is_allowed():
    result = bouncer.scan("just a normal commit message about refactoring auth")
    assert result.safe is True
    assert result.decision == "allow"
    assert result.findings == []


def test_detects_token_types_with_line_numbers():
    text = "ok\nopenai = sk-abcdefghijklmnopqrstuvwxyz123456\nAKIAIOSFODNN7EXAMPLE\n"
    result = bouncer.scan(text)
    kinds = {f.kind for f in result.findings}
    assert "openai_key" in kinds
    assert "aws_access_key_id" in kinds
    assert any(f.line == 2 for f in result.findings)


def test_redact_mode_replaces_secret_and_flags_redact():
    text = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    result = bouncer.scan(text, redact=True)
    assert result.decision == "redact"
    assert result.safe is False
    assert "ghp_abcdefghij" not in result.redacted_text
    assert "[REDACTED:github_token]" in result.redacted_text


def test_flags_high_entropy_unknown_secret():
    # a random-looking credential matching none of the known patterns
    text = "DB_PASSWORD=9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd0Sg3Hj"
    result = bouncer.scan(text)
    kinds = {f.kind for f in result.findings}
    assert "high_entropy" in kinds


def test_redact_also_removes_high_entropy_secret():
    # a detected secret must never survive redaction, even an unknown-format one
    secret = "9f3Kx7Qz2Lm8Rt5Vw1Yb4Nc6Pd"
    result = bouncer.scan(f"dbpass={secret}", redact=True)
    assert secret not in result.redacted_text
    assert "[REDACTED" in result.redacted_text


def test_ordinary_prose_is_not_flagged_as_entropy():
    text = ("the quick brown fox jumps over the lazy dog while refactoring "
            "the authentication and authorization modules thoroughly")
    result = bouncer.scan(text)
    assert result.findings == []
