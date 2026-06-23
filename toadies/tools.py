"""Tool dispatch — the agent-callable surface of the Toadies.

Pure and transport-agnostic: maps an MCP tool name + arguments to a JSON-serializable
result by calling the underlying Toadies (gremlin, bouncer, trust, accountant). The MCP
stdio server (mcp_server.py) is a thin wrapper over this; everything here is unit-tested
without any protocol or network.
"""

from __future__ import annotations

from dataclasses import asdict

from . import accountant, bouncer, config, gremlin, trust
from .store import Store


class ToolError(Exception):
    """Unknown tool or bad arguments."""


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
    raise ToolError(f"unknown tool: {name}")


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
    s = Store(db_path or config.default_db_path())
    try:
        return {"ok": True, "toadie": "accountant",
                "rows": accountant.status(s),
                "summary_markdown": accountant.render_status(s)}
    finally:
        s.close()


def _submit_grade(args, db_path):
    for required in ("toadie", "task_type", "score"):
        if required not in args:
            raise ToolError(f"submit_grade requires `{required}`")
    s = Store(db_path or config.default_db_path())
    try:
        state = trust.record_grade(s, args["toadie"], args["task_type"], args["score"],
                                   source=args.get("source", "rubric"))
    finally:
        s.close()
    return {"ok": True, "toadie": "accountant", "leash_level": state.leash_level,
            "ema": state.ema, "samples": state.samples}
