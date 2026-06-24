"""Bouncer — deterministic secret/safety guard.

DELIBERATELY NOT LLM-GATED. Secret detection must be deterministic (regex + entropy):
a 1B local model would miss keys, and the whole point is to never leak credentials
upstream. An LLM may later act as a *secondary* opinion, never as the gate.
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Default (repo-root) location of the agent-tunable advisory config; overridable via
# $BOUNCER_ADVISORY. The gate ($BOUNCER_GATE) has no repo default — it stays in code
# unless the strong tier deliberately supplies a gate file.
DEFAULT_ADVISORY_PATH = str(Path(__file__).resolve().parent.parent / "bouncer_advisory.toml")

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

# Tunable defaults (baked-in fallback when no config file is present).
_DEFAULT_BLOCK_SEVERITIES = ("critical", "high")
_DEFAULT_WARN_SEVERITIES = ("medium",)
_DEFAULT_BENIGN = ("diff_header_path", "filesystem_path", "date_slug", "uuid")


@dataclass
class BouncerConfig:
    entropy_min_len: int
    entropy_bits: float
    patterns: list                      # (kind, severity, compiled_pattern)
    block_severities: tuple
    warn_severities: tuple
    benign: tuple                        # enabled recognizer names
    benign_custom: list = field(default_factory=list)  # (name, compiled_regex)


class BouncerConfigError(Exception):
    """Invalid config edit (e.g. an attempt to write the gate via the advisory API)."""


# Keys that belong to the hard gate — NOT writable through the agent-facing API.
_GATE_KEYS = ("patterns", "block_severities")


def _read_toml(path):
    if not path:
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}


def _toml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise BouncerConfigError(f"unsupported config value: {v!r}")


def _toml_value(v):
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    return _toml_scalar(v)


def _dump_toml(data):
    """Minimal TOML writer for the advisory schema (scalars, arrays, tables, array-of-tables)."""
    lines = []
    for k, v in data.items():
        if not isinstance(v, (dict, list)) or (isinstance(v, list) and not (v and isinstance(v[0], dict))):
            lines.append(f"{k} = {_toml_value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"\n[{k}]")
            for kk, vv in v.items():
                if isinstance(vv, list) and vv and isinstance(vv[0], dict):
                    for item in vv:
                        lines.append(f"\n[[{k}.{kk}]]")
                        lines += [f"{ik} = {_toml_value(iv)}" for ik, iv in item.items()]
                else:
                    lines.append(f"{kk} = {_toml_value(vv)}")
    return "\n".join(lines) + "\n"


def config_view(advisory_path=None, gate_path=None) -> dict:
    """Read-only JSON-able view of the effective config (advisory + gate)."""
    cfg = load_config(advisory_path, gate_path)
    return {
        "entropy": {"min_len": cfg.entropy_min_len, "bits": cfg.entropy_bits},
        "policy": {"warn_severities": list(cfg.warn_severities)},
        "benign": {"enabled": list(cfg.benign), "custom": [n for n, _ in cfg.benign_custom]},
        "gate": {
            "block_severities": list(cfg.block_severities),
            "patterns": [k for k, _s, _p in cfg.patterns],
        },
    }


def set_advisory(patch: dict, advisory_path=None) -> dict:
    """Merge `patch` into the advisory config file. Refuses any gate key (§7)."""
    bad = [k for k in patch if k in _GATE_KEYS]
    if bad:
        raise BouncerConfigError(f"gate keys are not writable via this API: {bad}")
    path = advisory_path or os.environ.get("BOUNCER_ADVISORY") or DEFAULT_ADVISORY_PATH
    merged = {**_read_toml(path), **patch}
    with open(path, "w") as fh:
        fh.write(_dump_toml(merged))
    return config_view(advisory_path=path)


def load_config(advisory_path=None, gate_path=None) -> BouncerConfig:
    """Build effective config: baked-in defaults, overridden by the advisory + gate files."""
    advisory_path = advisory_path or os.environ.get("BOUNCER_ADVISORY") or DEFAULT_ADVISORY_PATH
    gate_path = gate_path or os.environ.get("BOUNCER_GATE")
    adv = _read_toml(advisory_path)
    gate = _read_toml(gate_path)

    entropy = adv.get("entropy", {})
    policy = adv.get("policy", {})
    benign_cfg = adv.get("benign", {})

    patterns = list(_PATTERNS)
    if "patterns" in gate:
        patterns = [
            (p["kind"], p["severity"], re.compile(p["regex"])) for p in gate["patterns"]
        ]

    return BouncerConfig(
        entropy_min_len=entropy.get("min_len", _ENTROPY_MIN_LEN),
        entropy_bits=entropy.get("bits", _ENTROPY_BITS),
        patterns=patterns,
        block_severities=tuple(gate.get("block_severities", _DEFAULT_BLOCK_SEVERITIES)),
        warn_severities=tuple(policy.get("warn_severities", _DEFAULT_WARN_SEVERITIES)),
        benign=tuple(benign_cfg.get("enabled", _DEFAULT_BENIGN)),
        benign_custom=[(c["name"], re.compile(c["regex"])) for c in benign_cfg.get("custom", [])],
    )


@dataclass
class Finding:
    kind: str
    severity: str
    line: int
    message: str
    score: float | None = None         # entropy bits for high_entropy findings; None for patterns


@dataclass
class ScanResult:
    safe: bool
    decision: str                      # 'allow' | 'block' | 'redact'
    findings: list = field(default_factory=list)
    redacted_text: str | None = None


def scan(text, *, redact=False, config=None) -> ScanResult:
    config = config or load_config()
    findings = []
    for idx, line in enumerate(text.splitlines(), start=1):
        for kind, severity, pattern in _PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    kind=kind, severity=severity, line=idx,
                    message=f"{kind.replace('_', ' ')} detected",
                ))

        for token in _TOKEN_RE.findall(line):
            if (_looks_like_secret(token) and not _matches_known(token)
                    and not _is_benign(token, config)):
                findings.append(Finding(
                    kind="high_entropy", severity="medium", line=idx,
                    message="high-entropy string (possible credential)",
                    score=_shannon_entropy(token),
                ))

    if not findings:
        return ScanResult(safe=True, decision="allow", findings=[])

    if redact:
        return ScanResult(
            safe=False, decision="redact", findings=findings,
            redacted_text=_redact(text),
        )

    # Two-tier verdict: gate-severity findings (patterns) BLOCK; the rest (entropy) WARN.
    if any(f.severity in config.block_severities for f in findings):
        return ScanResult(safe=False, decision="block", findings=findings)
    return ScanResult(safe=True, decision="warn", findings=findings)


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


# --- Benign-shape filter: drop high-entropy tokens that are recognizable structures. ---
# Only ever runs in the advisory (entropy) lane — never suppresses a pattern match.
_WORDLIKE_SEG = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")
_DATE_SLUG_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _is_filesystem_path(token):
    # Structural + base64-safe: a path is >=2 '/'-segments each of which is itself a
    # lowercase word/number group (base64 has mixed case and +/= chars, so it fails).
    if "/" not in token or "+" in token or "=" in token:
        return False
    segments = [s for s in token.split("/") if s]
    return len(segments) >= 2 and all(_WORDLIKE_SEG.match(s) for s in segments)


_BENIGN_RECOGNIZERS = {
    "filesystem_path": _is_filesystem_path,
    "diff_header_path": lambda t: t.startswith(("a/", "b/")),
    "date_slug": lambda t: _DATE_SLUG_RE.search(t) is not None,
    "uuid": lambda t: _UUID_RE.search(t) is not None,
}


def _is_benign(token, config):
    for name in config.benign:
        recognizer = _BENIGN_RECOGNIZERS.get(name)
        if recognizer and recognizer(token):
            return True
    return any(regex.search(token) for _name, regex in config.benign_custom)


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
