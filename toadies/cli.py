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

from . import accountant, bouncer, config, gremlin, interjection, localai, reviewer, trust
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
    try:
        score = trust._coerce_score(args.score)
        state = None
        try:
            s = Store(args.db)
            try:
                state = trust.record_grade(
                    s,
                    args.toadie,
                    args.task_type,
                    score,
                    source=args.source,
                )
            finally:
                s.close()
        except Exception as exc:
            state = trust.CompetencyState(args.toadie, args.task_type, 0.0, 0, "probation")
            print("grade: trust store unavailable; defaulting to probation")
            print(f"grade: {exc}")
    except ValueError as exc:
        print(f"grade error: {exc}")
        return 2
    if state is None:
        state = trust.CompetencyState(args.toadie, args.task_type, 0.0, 0, "probation")
    print(f"{state.toadie}/{state.task_type}: {state.leash_level.upper()} "
          f"(ema {state.ema:.2f}, n={state.samples})")
    return 0


def _cmd_accountant(args) -> int:
    try:
        s = Store(args.db)
        try:
            if args.action == "status":
                print(accountant.render_status(s))
        finally:
            s.close()
    except Exception as exc:
        if args.action == "status":
            print("accountant: trust store unavailable; defaulting to probation")
            print(f"accountant error: {exc}")
            print("No Toadies have been graded yet — every Toadie starts on probation.")
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


def _cmd_judge(args) -> int:
    output_text = _read_input(args.output_file)
    input_text = args.input_file_text
    if args.input_file is not None:
        input_text = _read_input(args.input_file)

    try:
        result = reviewer.review_and_record(
            args.toadie,
            args.task_type,
            input_text,
            output_text,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            dataset_path=args.dataset_path,
            db_path=args.db,
        )
    except Exception as exc:
        print(f"judge error: {exc}")
        return 2

    if args.json:
        print(json.dumps(result))
    else:
        if result.get("ok"):
            print(
                f"judge: {args.toadie}/{args.task_type}: score {result['score']:.2f} "
                f"-> {result['leash_level'].upper()} "
                f"(ema {result['ema']:.2f}, n={result['samples']})"
            )
        else:
            print(f"judge: {args.toadie}/{args.task_type}: score {result['score']:.2f} (unpersisted)")
            print(f"judge error: {result.get('error')}")
    return 0


def _cmd_interjections(args) -> int:
    rows = interjection.list_interjections(
        args.db,
        limit=args.limit,
        since_created_at=args.since,
        toadie=args.toadie,
        task_type=args.task_type,
        urgency=args.urgency,
        delivery=args.delivery,
    )

    if args.json:
        print(json.dumps(rows))
        return 0

    if not rows:
        print("interjections: none queued")
        return 0

    for row in rows:
        since = row.get("created_at", "")
        prefix = f"[{row.get('delivery', '?')}/{row.get('urgency', '?')}]"
        actor = row.get("toadie") or "unknown"
        task = row.get("task_type") or "generic"
        message = row.get("message") or ""
        print(f"{since} {prefix} {actor}/{task}: {message}")
        if row.get("details"):
            print(f"  details: {row['details']}")
        if row.get("task_context"):
            print(f"  context: {row['task_context']}")
        if row.get("trust"):
            trust_state = row["trust"]
            ema = trust_state.get("ema")
            samples = trust_state.get("samples")
            ema_text = f"{ema:.2f}" if isinstance(ema, (int, float)) else "n/a"
            samples_text = str(samples) if isinstance(samples, int) else "n/a"
            print(
                f"  trust: level={trust_state.get('leash_level')} "
                f"ema={ema_text} "
                f"samples={samples_text}"
            )
    return 0


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

    jg = sub.add_parser("judge", help="run a judge model on output and record trust grade")
    jg.add_argument("toadie", help="toadie under review")
    jg.add_argument("task_type", help="task bucket, e.g. pytest")
    jg.add_argument("output_file", help="file containing the output to judge, or '-' for stdin")
    jg.add_argument("--input-file", dest="input_file", default=None, help="optional input/reference text file")
    jg.add_argument("--input-file-text", dest="input_file_text", default="", help="inline reference text")
    jg.add_argument("--model", default=reviewer.DEFAULT_JUDGE_MODEL, help="judge model id")
    jg.add_argument("--base-url", default=localai.DEFAULT_BASE_URL, help="judge endpoint")
    jg.add_argument("--api-key", default=None, help="judge API key")
    jg.add_argument("--timeout", type=int, default=reviewer.DEFAULT_JUDGE_TIMEOUT,
                     help=f"judge request timeout in seconds (default {reviewer.DEFAULT_JUDGE_TIMEOUT})")
    jg.add_argument("--max-tokens", type=int, default=reviewer.DEFAULT_JUDGE_MAX_TOKENS,
                     help=f"max output tokens for judge response (default {reviewer.DEFAULT_JUDGE_MAX_TOKENS})")
    jg.add_argument("--dataset-path", default=None, help="optional dataset log output path (JSONL)")
    jg.add_argument("--db", default=config.default_db_path())
    jg.add_argument("--json", action="store_true")
    jg.set_defaults(func=_cmd_judge)

    ix = sub.add_parser("interjections", help="inspect the toadie interjection queue")
    ix.add_argument("--db", default=config.default_db_path())
    ix.add_argument("--limit", type=int, default=20)
    ix.add_argument("--since", default=None, help="ISO-8601 lower-bound created_at")
    ix.add_argument("--toadie", default=None, help="filter by toadie")
    ix.add_argument("--task-type", dest="task_type", default=None, help="filter by task type")
    ix.add_argument("--urgency", default=None, choices=sorted(interjection.INTERJECTION_URGENCY))
    ix.add_argument("--delivery", default=None, choices=[interjection.DELIVERY_APPEND, interjection.DELIVERY_INTERRUPT])
    ix.add_argument("--json", action="store_true")
    ix.set_defaults(func=_cmd_interjections)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
