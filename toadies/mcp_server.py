"""Minimal MCP stdio server for the Toadies — dependency-free.

Speaks newline-delimited JSON-RPC 2.0 over stdin/stdout (the MCP stdio transport).
Protocol handling is kept in the pure `handle_message`, so it is unit-tested without
real pipes; `main()` is just the read/dispatch/write loop. The actual tool work lives
in tools.py.

Register with Codex:  codex mcp add toadies -- python3 -m toadies.mcp_server
"""

from __future__ import annotations

import json
import sys

from . import tools

SERVER_INFO = {"name": "toadies", "version": "0.0.1"}
DEFAULT_PROTOCOL = "2025-06-18"

_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def _result(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle_message(message, *, db_path=None):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    method = message.get("method")
    mid = message.get("id")

    # Notifications (no id) get no response.
    if mid is None and method != "initialize":
        return None

    if method == "initialize":
        params = message.get("params") or {}
        return _result(mid, {
            "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        return _result(mid, {"tools": tools.list_tools()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            output = tools.dispatch(name, arguments, db_path=db_path)
            text = json.dumps(output)
            return _result(mid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            return _result(mid, {
                "content": [{"type": "text", "text": f"tool error: {exc}"}],
                "isError": True,
            })

    return _error(mid, _METHOD_NOT_FOUND, f"method not found: {method}")


def main(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
