"""Gremlin — the log/test-output compressor.

Deterministic-first: it never needs an LLM to find the signal in noisy test/build
output. It strips ANSI, drops repetitive progress noise, and surfaces the lines that
actually matter (failures, errors, tracebacks, file:line references), preserving raw
paths and line numbers verbatim so Robot can ask for more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Strip ANSI color/escape sequences before analysis.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Lines worth keeping, with a coarse "kind" tag. Order matters: first match wins.
_SIGNAL_PATTERNS = [
    ("test_failure", re.compile(r"^FAILED\s")),
    ("test_failure", re.compile(r"^E\s")),               # pytest assertion explanation
    ("assertion", re.compile(r"^>?\s*assert\b")),
    ("traceback", re.compile(r"^Traceback\b")),
    ("error", re.compile(r"\b(Error|Exception)\b")),
    ("error", re.compile(r"\berror:\s")),                # compiler / linker style
    ("file_ref", re.compile(r"\b[\w./-]+:\d+\b")),       # path:line
    ("summary", re.compile(r"\b\d+\s+failed\b")),
]


# Lower number = more important; decides what survives when over budget.
_KIND_PRIORITY = {
    "test_failure": 0,
    "assertion": 1,
    "traceback": 2,
    "error": 3,
    "summary": 4,
    "file_ref": 5,
}


@dataclass
class Finding:
    kind: str
    line: int          # 1-based line number in the original text
    text: str


@dataclass
class GremlinResult:
    summary_markdown: str
    top_findings: list[Finding] = field(default_factory=list)
    original_chars: int = 0
    summary_chars: int = 0
    toadie: str = "gremlin"


def _classify(line: str) -> str | None:
    for kind, pattern in _SIGNAL_PATTERNS:
        if pattern.search(line):
            return kind
    return None


def compress(text, *, source_hint=None, max_chars=6000) -> GremlinResult:
    original_chars = len(text)
    clean = _ANSI_RE.sub("", text)

    findings: list[Finding] = []
    for idx, raw_line in enumerate(clean.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        kind = _classify(line)
        if kind is not None:
            findings.append(Finding(kind=kind, line=idx, text=line.strip()))

    header = ["# Gremlin summary"]
    if source_hint:
        header.append(f"_source: {source_hint}_")
    header.append("")

    if not findings:
        header.append("No failures or errors detected.")
        summary_markdown = "\n".join(header)
        return GremlinResult(
            summary_markdown=summary_markdown,
            top_findings=findings,
            original_chars=original_chars,
            summary_chars=len(summary_markdown),
        )

    def render(f: Finding) -> str:
        return f"- L{f.line} [{f.kind}] {f.text}"

    # Select the most important findings that fit the budget, reserving room for a
    # truncation note. Choose by severity, but render in original line order.
    by_priority = sorted(findings, key=lambda f: (_KIND_PRIORITY.get(f.kind, 9), f.line))
    base_len = len("\n".join(header + [f"{len(findings)} notable line(s):", ""]))
    note_reserve = 80  # room for the "... truncated" line
    selected: list[Finding] = []
    used = base_len
    for f in by_priority:
        cost = len(render(f)) + 1
        if used + cost > max_chars - note_reserve:
            continue
        selected.append(f)
        used += cost

    selected.sort(key=lambda f: f.line)
    omitted = len(findings) - len(selected)

    body = [f"{len(findings)} notable line(s):", ""]
    body.extend(render(f) for f in selected)
    if omitted:
        body.append(f"_… {omitted} more line(s) truncated to fit the budget._")

    summary_markdown = "\n".join(header + body)

    # A compressor must never inflate. If the framed summary would be larger than
    # the input, the input is already compact — return it (ANSI-stripped) as-is.
    if len(summary_markdown) > original_chars:
        summary_markdown = clean

    return GremlinResult(
        summary_markdown=summary_markdown,
        top_findings=findings,
        original_chars=original_chars,
        summary_chars=len(summary_markdown),
    )
