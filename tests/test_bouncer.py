from toadies import bouncer


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
