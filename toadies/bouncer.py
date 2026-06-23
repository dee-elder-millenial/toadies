"""Bouncer — deterministic secret/safety guard.

DELIBERATELY NOT LLM-GATED. Secret detection must be deterministic (regex + entropy):
a 1B local model would miss keys, and the whole point is to never leak credentials
upstream. An LLM may later act as a *secondary* opinion, never as the gate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# High-entropy detection thresholds (deterministic, no model).
_ENTROPY_MIN_LEN = 20
_ENTROPY_BITS = 3.5
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{%d,}" % _ENTROPY_MIN_LEN)

# (kind, severity, compiled pattern). Severity: critical >= high >= medium.
_PATTERNS = [
    ("private_key", "critical", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws_secret", "critical", re.compile(r"AWS_SECRET_ACCESS_KEY")),
    ("aws_access_key_id", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", "high", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("openai_key", "high", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
]

_SEVERITY_RANK = {"medium": 1, "high": 2, "critical": 3}


@dataclass
class Finding:
    kind: str
    severity: str
    line: int
    message: str


@dataclass
class ScanResult:
    safe: bool
    decision: str                      # 'allow' | 'block' | 'redact'
    findings: list = field(default_factory=list)
    redacted_text: str | None = None


def scan(text, *, redact=False) -> ScanResult:
    findings = []
    for idx, line in enumerate(text.splitlines(), start=1):
        for kind, severity, pattern in _PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    kind=kind, severity=severity, line=idx,
                    message=f"{kind.replace('_', ' ')} detected",
                ))

        for token in _TOKEN_RE.findall(line):
            if _looks_like_secret(token) and not _matches_known(token):
                findings.append(Finding(
                    kind="high_entropy", severity="medium", line=idx,
                    message="high-entropy string (possible credential)",
                ))

    if not findings:
        return ScanResult(safe=True, decision="allow", findings=[])

    if redact:
        return ScanResult(
            safe=False, decision="redact", findings=findings,
            redacted_text=_redact(text),
        )
    return ScanResult(safe=False, decision="block", findings=findings)


def _shannon_entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_like_secret(token):
    # Require length, mixed char classes (letters AND digits), and high entropy.
    # This discriminates random credentials from long ordinary words (no digits).
    if len(token) < _ENTROPY_MIN_LEN:
        return False
    has_alpha = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_alpha and has_digit):
        return False
    return _shannon_entropy(token) >= _ENTROPY_BITS


def _matches_known(token):
    return any(pattern.search(token) for _kind, _sev, pattern in _PATTERNS)


def _redact(text):
    out = text
    for kind, _severity, pattern in _PATTERNS:
        out = pattern.sub(f"[REDACTED:{kind}]", out)
    # also blank any high-entropy token that survived the known patterns
    def _maybe(m):
        tok = m.group(0)
        if _looks_like_secret(tok) and not _matches_known(tok):
            return "[REDACTED:high_entropy]"
        return tok
    return _TOKEN_RE.sub(_maybe, out)
