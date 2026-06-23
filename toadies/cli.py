"""toadiectl — direct shell access to the Toadies.

MVP surface: `toadiectl gremlin <file|->`. Reads a file (or stdin with `-`),
runs the deterministic compressor, and prints either a Markdown summary (default)
or a structured JSON payload (`--json`) suitable for feeding back into an agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from dataclasses import asdict as _asdict

from . import accountant, bouncer, config, gremlin, trust
from .store import Store


def _read_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    with open(source, "r", errors="replace") as fh:
        return fh.read()


def _cmd_gremlin(args) -> int:
    text = _read_input(args.path)
    result = gremlin.compress(text, source_hint=args.source_hint, max_chars=args.max_chars)
    if args.json:
        payload = {
            "ok": True,
            "toadie": result.toadie,
            "summary_markdown": result.summary_markdown,
            "top_findings": [asdict(f) for f in result.top_findings],
            "original_chars": result.original_chars,
            "summary_chars": result.summary_chars,
        }
        print(json.dumps(payload))
    else:
        print(result.summary_markdown)
    return 0


def _cmd_grade(args) -> int:
    s = Store(args.db)
    try:
        state = trust.record_grade(s, args.toadie, args.task_type, args.score, source=args.source)
    finally:
        s.close()
    print(f"{state.toadie}/{state.task_type}: {state.leash_level.upper()} "
          f"(ema {state.ema:.2f}, n={state.samples})")
    return 0


def _cmd_accountant(args) -> int:
    s = Store(args.db)
    try:
        if args.action == "status":
            print(accountant.render_status(s))
    finally:
        s.close()
    return 0


def _cmd_bouncer(args) -> int:
    if args.text is not None:
        text = args.text
    elif args.file:
        text = _read_input(args.file)
    else:
        text = sys.stdin.read()

    result = bouncer.scan(text, redact=args.redact)
    if args.json:
        print(json.dumps({
            "ok": True,
            "toadie": "bouncer",
            "safe": result.safe,
            "decision": result.decision,
            "findings": [_asdict(f) for f in result.findings],
            "redacted_text": result.redacted_text,
        }))
    elif result.safe:
        print("bouncer: allow — no secrets detected")
    else:
        print(f"bouncer: {result.decision} — {len(result.findings)} finding(s):")
        for f in result.findings:
            print(f"  [{f.severity}] {f.kind} at line {f.line}")
        if result.redacted_text is not None:
            print("--- redacted ---")
            print(result.redacted_text)
    return 0 if result.safe else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toadiectl", description="Local Toadies sidecar.")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gremlin", help="compress noisy log / test output")
    g.add_argument("path", help="file to compress, or '-' for stdin")
    g.add_argument("--json", action="store_true", help="emit structured JSON")
    g.add_argument("--source-hint", default=None, help="e.g. 'npm test'")
    g.add_argument("--max-chars", type=int, default=6000, dest="max_chars")
    g.set_defaults(func=_cmd_gremlin)

    gr = sub.add_parser("grade", help="record a competency grade for a Toadie")
    gr.add_argument("toadie")
    gr.add_argument("task_type")
    gr.add_argument("score", type=float, help="0.0..1.0")
    gr.add_argument("--source", default="rubric", choices=["rubric", "outcome"])
    gr.add_argument("--db", default=config.default_db_path())
    gr.set_defaults(func=_cmd_grade)

    ac = sub.add_parser("accountant", help="budget / trust-ladder status")
    ac.add_argument("action", choices=["status"])
    ac.add_argument("--db", default=config.default_db_path())
    ac.set_defaults(func=_cmd_accountant)

    bo = sub.add_parser("bouncer", help="scan text/file/stdin for secrets")
    bo.add_argument("--text", default=None, help="scan this literal string")
    bo.add_argument("--file", default=None, help="scan this file")
    bo.add_argument("--redact", action="store_true", help="emit redacted text instead of blocking")
    bo.add_argument("--json", action="store_true")
    bo.set_defaults(func=_cmd_bouncer)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
