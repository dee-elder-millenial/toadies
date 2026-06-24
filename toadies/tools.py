"""Tool dispatch — the agent-callable surface of the Toadies.

Pure and transport-agnostic: maps an MCP tool name + arguments to a JSON-serializable
result by calling the underlying Toadies (gremlin, bouncer, trust, accountant). The MCP
stdio server (mcp_server.py) is a thin wrapper over this; everything here is unit-tested
without any protocol or network.
"""

from __future__ import annotations

from dataclasses import asdict

from . import accountant, bouncer, config, gremlin, interjection, reviewer, trust
from .store import Store


class ToolError(Exception):
    """Unknown tool or bad arguments."""


def _submit_grade_error(toadie, task_type):
    return {
        "ok": False,
        "toadie": "accountant",
        "task_type": task_type,
        "leash_level": "probation",
        "ema": 0.0,
        "samples": 0,
    }


def list_tools():
    return [
        {
            "name": "gremlin_compress",
            "description": "Compress noisy logs / test output, preserving the failure signal "
                           "and raw references. Pass `text` or `path`.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "path": {"type": "string"},
                    "source_hint": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
            },
        },
        {
            "name": "bouncer_scan",
            "description": "Scan text for secrets/credentials before it goes upstream. "
                           "Returns allow/block/redact and findings.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "redact": {"type": "boolean"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "accountant_status",
            "description": "Report the Toadie trust ladder (competency + leash level per task type).",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "submit_grade",
            "description": "Record a competency grade for a Toadie (the paid model judging local "
                           "output). score 0..1; source 'rubric' or 'outcome'.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "toadie": {"type": "string"},
                    "task_type": {"type": "string"},
                    "score": {"type": "number"},
                    "source": {"type": "string", "enum": ["rubric", "outcome"]},
                },
                "required": ["toadie", "task_type", "score"],
            },
        },
        {
            "name": "judge_and_grade",
            "description": "Ask a judge model to grade a Toadie output and persist the grade in trust.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "toadie": {"type": "string"},
                    "task_type": {"type": "string"},
                    "input_text": {"type": "string"},
                    "output_text": {"type": "string"},
                    "model": {"type": "string"},
                    "base_url": {"type": "string"},
                    "api_key": {"type": "string"},
                    "source": {"type": "string", "enum": ["rubric", "outcome"]},
                    "dataset_path": {"type": "string"},
                },
                "required": ["toadie", "task_type", "output_text"],
            },
        },
        {
            "name": "toadie_interject",
            "description": "Submit a high-confidence finding from a toadie to the interjection channel.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "toadie": {"type": "string"},
                    "task_type": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "requested_delivery": {"type": "string", "enum": ["auto", "interrupt", "append"]},
                    "task_context": {"type": "object"},
                },
                "required": ["toadie", "task_type", "message"],
            },
        },
        {
            "name": "toadie_interjection_inbox",
            "description": "Read latest interjections for Robot. Filter by urgency/delivery/toadie/task_type.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "toadie": {"type": "string"},
                    "task_type": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "delivery": {"type": "string", "enum": ["interrupt", "append"]},
                    "since_created_at": {"type": "string"},
                },
            },
        },
        {
            "name": "toady_dispatch",
            "description": "Invoke any registered toady by name through the distribution "
                           "layer (registry routing: in-process for deterministic toadies, "
                           "Ollama for model-backed ones, with fall-back to the GPU box). "
                           "Pass `toady` and a `payload` object.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "toady": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["toady"],
            },
        },
    ]


def dispatch(name, arguments, *, db_path=None):
    if name == "gremlin_compress":
        return _gremlin_compress(arguments)
    if name == "bouncer_scan":
        return _bouncer_scan(arguments)
    if name == "accountant_status":
        return _accountant_status(db_path)
    if name == "submit_grade":
        return _submit_grade(arguments, db_path)
    if name == "judge_and_grade":
        return _judge_and_grade(arguments, db_path)
    if name == "toadie_interject":
        return _toadie_interject(arguments, db_path)
    if name == "toadie_interjection_inbox":
        return _toadie_interjection_inbox(arguments, db_path)
    if name == "toady_dispatch":
        return _toady_dispatch(arguments)
    raise ToolError(f"unknown tool: {name}")


def _toady_dispatch(args):
    # Lazy import: dispatch.py imports tools, so importing at module load would cycle.
    from . import dispatch
    toady = args.get("toady")
    if not toady:
        raise ToolError("toady_dispatch requires 'toady'")
    return dispatch.run(toady, args.get("payload", {}))


def _gremlin_compress(args):
    text = args.get("text")
    if text is None and args.get("path"):
        with open(args["path"], "r", errors="replace") as fh:
            text = fh.read()
    if text is None:
        raise ToolError("gremlin_compress requires `text` or `path`")
    result = gremlin.compress(text, source_hint=args.get("source_hint"),
                              max_chars=args.get("max_chars", 6000))
    return {
        "ok": True,
        "toadie": "gremlin",
        "summary_markdown": result.summary_markdown,
        "top_findings": [asdict(f) for f in result.top_findings],
        "original_chars": result.original_chars,
        "summary_chars": result.summary_chars,
    }


def _bouncer_scan(args):
    if "text" not in args:
        raise ToolError("bouncer_scan requires `text`")
    result = bouncer.scan(args["text"], redact=args.get("redact", False))
    return {
        "ok": True,
        "toadie": "bouncer",
        "safe": result.safe,
        "decision": result.decision,
        "findings": [asdict(f) for f in result.findings],
        "redacted_text": result.redacted_text,
    }


def _accountant_status(db_path):
    db_path = db_path or config.default_db_path()
    try:
        s = Store(db_path)
        try:
            rows = accountant.status(s)
            return {"ok": True, "toadie": "accountant",
                    "rows": rows,
                    "summary_markdown": accountant.render_status(s)}
        finally:
            s.close()
    except Exception as exc:
        return {
            "ok": False,
            "toadie": "accountant",
            "rows": [],
            "summary_markdown": (
                "Accountant data store unavailable. "
                "Trust defaults to probation and review is treated as unsafe."
            ),
            "error": str(exc),
        }


def _submit_grade(args, db_path):
    for required in ("toadie", "task_type", "score"):
        if required not in args:
            raise ToolError(f"submit_grade requires `{required}`")
    try:
        score = trust._coerce_score(args["score"])
        source = trust._coerce_source(args.get("source", "rubric"))
    except ValueError as exc:
        raise ToolError(str(exc))

    db_path = db_path or config.default_db_path()
    try:
        s = Store(db_path)
        try:
            state = trust.record_grade(
                s,
                args["toadie"],
                args["task_type"],
                score,
                source=source,
            )
        finally:
            s.close()
    except Exception as exc:
        return {
            **_submit_grade_error(args["toadie"], args["task_type"]),
            "error": str(exc),
            "source": source,
            "score": score,
        }
    return {"ok": True, "toadie": "accountant", "leash_level": state.leash_level,
            "ema": state.ema, "samples": state.samples}


def _judge_and_grade(args, db_path):
    for required in ("toadie", "task_type", "output_text"):
        if required not in args:
            raise ToolError(f"judge_and_grade requires `{required}`")

    try:
        output_text = str(args["output_text"])
        input_text = str(args.get("input_text", ""))
        model = args.get("model", reviewer.DEFAULT_JUDGE_MODEL)
        base_url = args.get("base_url", None)
        api_key = args.get("api_key", None)
        source = trust._coerce_source(args.get("source", "rubric"))
        dataset_path = args.get("dataset_path", None)
        if dataset_path is not None:
            dataset_path = str(dataset_path)
    except Exception as exc:
        raise ToolError(f"judge_and_grade argument error: {exc}")

    return reviewer.review_and_record(
        args["toadie"],
        args["task_type"],
        input_text,
        output_text,
        model=model,
        base_url=base_url,
        api_key=api_key,
        source=source,
        db_path=db_path or config.default_db_path(),
        dataset_path=dataset_path,
    )


def _toadie_interject(args, db_path):
    for required in ("toadie", "task_type", "message"):
        if required not in args:
            raise ToolError(f"toadie_interject requires `{required}`")

    return interjection.post_interjection(
        args["toadie"],
        args["task_type"],
        message=args["message"],
        details=args.get("details"),
        urgency=args.get("urgency", "medium"),
        requested_delivery=args.get("requested_delivery", "auto"),
        task_context=args.get("task_context"),
        db_path=db_path or config.default_db_path(),
    )


def _toadie_interjection_inbox(args, db_path):
    return {
        "ok": True,
        "interjections": interjection.list_interjections(
            db_path=db_path or config.default_db_path(),
            limit=args.get("limit", 20),
            toadie=args.get("toadie"),
            task_type=args.get("task_type"),
            urgency=args.get("urgency"),
            delivery=args.get("delivery"),
            since_created_at=args.get("since_created_at"),
        ),
    }
