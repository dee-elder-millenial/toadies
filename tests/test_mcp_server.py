import json

from toadies import mcp_server


def _req(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_initialize_echoes_protocol_and_advertises_tools():
    resp = mcp_server.handle_message(
        _req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}))
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "toadies"


def test_tools_list_returns_the_tool_definitions():
    resp = mcp_server.handle_message(_req("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "gremlin_compress" in names and "bouncer_scan" in names


def test_tools_call_runs_the_tool_and_returns_text_content():
    resp = mcp_server.handle_message(_req("tools/call", {
        "name": "bouncer_scan",
        "arguments": {"text": "-----BEGIN RSA PRIVATE KEY-----"},
    }))
    content = resp["result"]["content"]
    assert content[0]["type"] == "text"
    payload = json.loads(content[0]["text"])
    assert payload["decision"] == "block"


def test_tools_call_unknown_tool_is_flagged_iserror():
    resp = mcp_server.handle_message(_req("tools/call", {"name": "nope", "arguments": {}}))
    assert resp["result"]["isError"] is True


def test_notification_returns_no_response():
    assert mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_returns_jsonrpc_error():
    resp = mcp_server.handle_message(_req("frobnicate"))
    assert resp["error"]["code"] == -32601
