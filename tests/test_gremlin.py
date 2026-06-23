from toadies.gremlin import compress


def _noisy_pytest_log():
    """A realistic, mostly-noise pytest run with one real failure buried in it."""
    lines = []
    lines.append("============================= test session starts ==============================")
    lines.append("platform linux -- Python 3.14.4, pytest-9.0.2, pluggy-1.5.0")
    lines.append("collected 247 items")
    lines.append("")
    # lots of passing-dot noise
    for i in range(200):
        lines.append(f"tests/test_module_{i}.py ....................                         [{i}%]")
    lines.append("")
    lines.append("=================================== FAILURES ===================================")
    lines.append("_________________________ test_refresh_token_expiry _________________________")
    lines.append("")
    lines.append("    def test_refresh_token_expiry():")
    lines.append("        resp = client.post('/auth/refresh')")
    lines.append(">       assert resp.status_code == 401")
    lines.append("E       assert 200 == 401")
    lines.append("")
    lines.append("tests/test_auth.py:482: AssertionError")
    lines.append("=========================== short test summary info ============================")
    lines.append("FAILED tests/test_auth.py::test_refresh_token_expiry - assert 200 == 401")
    lines.append("======================== 1 failed, 246 passed in 12.34s ========================")
    return "\n".join(lines)


def test_compress_dramatically_reduces_noise_but_keeps_the_failure():
    raw = _noisy_pytest_log()
    result = compress(raw)

    # size accounting is reported and honest
    assert result.original_chars == len(raw)
    assert result.summary_chars == len(result.summary_markdown)

    # the whole point: at least an 80% reduction (spec success criterion)
    assert result.summary_chars < result.original_chars * 0.20

    # but the actual signal survives
    assert "test_refresh_token_expiry" in result.summary_markdown
    assert "200 == 401" in result.summary_markdown


def test_preserves_raw_file_path_and_line_number_as_a_finding():
    raw = _noisy_pytest_log()
    result = compress(raw)

    # the failing file:line reference must survive verbatim so Robot can open it
    refs = [f for f in result.top_findings if "tests/test_auth.py:482" in f.text]
    assert refs, "expected the path:line reference to be captured as a finding"
    # findings point back at where they were in the raw text (1-based)
    assert all(f.line >= 1 for f in result.top_findings)


def test_summary_respects_max_chars_budget_even_with_thousands_of_errors():
    # a pathological run: thousands of distinct error lines
    raw = "\n".join(f"E   error: thing number {i} blew up at module_{i}.py:{i}" for i in range(5000))
    budget = 4000
    result = compress(raw, max_chars=budget)

    assert result.summary_chars <= budget
    # even when truncated, it must say it truncated (no silent signal loss)
    assert "truncated" in result.summary_markdown.lower()


def test_never_inflates_a_small_already_compact_input():
    # short input that is all signal — there is nothing to compress
    raw = "E   assert 200 == 401\ntests/test_auth.py:482: AssertionError\n"
    result = compress(raw)

    # a compressor must never make its input bigger
    assert result.summary_chars <= result.original_chars
    assert "200 == 401" in result.summary_markdown
