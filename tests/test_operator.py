"""Operator safety tests.

These never move the real cursor or press real keys: they exercise the disabled path, the
dry-run path, bounds checks, the panic lock, and the audit log. The MCP surface is tested
through the CLI in its default (disabled) mode, which is the shipped default.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load_operator():
    spec = importlib.util.spec_from_file_location("operator_mcp", TOOLS / "operator_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["operator_mcp"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_input_is_disabled_by_default():
    op = load_operator()
    o = op.Operator()                      # no enable_input
    assert o.enable_input is False
    assert o.dry_run is True
    res = o.mouse_move(10, 10)
    assert res["ok"] is False and "--enable-input" in res["error"], res


def test_dry_run_reports_plan_without_applying():
    op = load_operator()
    o = op.Operator(enable_input=True, dry_run=True)
    move = o.mouse_move(5, 5)
    assert move["ok"] is True and move["applied"] is False and move["plan"] == {"move": [5, 5]}
    typ = o.key_type("hello")
    assert typ["ok"] is True and typ["applied"] is False
    press = o.key_press(["CTRL", "S"])
    assert press["ok"] is True and press["applied"] is False and press["plan"]["keys"] == ["CTRL", "S"]


def test_bounds_are_checked():
    op = load_operator()
    o = op.Operator(enable_input=True, dry_run=True)
    res = o.mouse_move(10 ** 9, 10 ** 9)
    assert res["ok"] is False and "out of bounds" in res["error"], res


def test_bad_arguments_are_rejected():
    op = load_operator()
    o = op.Operator(enable_input=True, dry_run=True)
    assert o.mouse_click(0, 0, button="laser")["ok"] is False
    assert o.mouse_click(0, 0, count=99)["ok"] is False
    assert o.key_press(["NOPE"])["ok"] is False
    assert o.key_type("")["ok"] is False
    assert o.key_press([])["ok"] is False


def test_panic_locks_the_session():
    op = load_operator()
    o = op.Operator(enable_input=True, dry_run=True)
    assert o.mouse_move(1, 1)["ok"] is True
    o.panic()
    res = o.mouse_move(1, 1)
    assert res["ok"] is False and "panic" in res["error"], res


def test_audit_log_records_actions(tmp_path):
    op = load_operator()
    log = tmp_path / "audit.jsonl"
    o = op.Operator(enable_input=True, dry_run=True, audit_path=log)
    o.mouse_move(3, 4)
    o.key_type("x")
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert [r["action"] for r in lines] == ["mouse_move", "key_type"], lines
    assert lines[0]["params"] == {"x": 3, "y": 4}


def test_screen_capture_shape():
    op = load_operator()
    o = op.Operator(enable_input=False)
    res = o.capture(max_width=640)
    if res["ok"]:
        assert res["png_base64"].startswith("iVBOR")     # PNG magic in base64
        assert res["width"] > 0 and res["height"] > 0
    else:
        assert "unsupported platform" in res["error"], res


def rpc(requests: list[dict], *args: str) -> list[dict]:
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run([sys.executable, str(TOOLS / "operator_mcp.py"), *args],
                          input=payload, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def test_mcp_default_mode_refuses_input():
    responses = rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "operator_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "mouse_move", "arguments": {"x": 1, "y": 1}}},
    ])
    names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert names == {"operator_status", "screen_capture", "mouse_move", "mouse_click",
                     "key_type", "key_press", "operator_panic"}, names

    status = json.loads(responses[2]["result"]["content"][0]["text"])
    assert status["input_enabled"] is False and status["dry_run"] is True, status

    move = json.loads(responses[3]["result"]["content"][0]["text"])
    assert move["ok"] is False and "disabled" in move["error"], move


def test_mcp_panic_tool_works_in_dry_run():
    responses = rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "operator_panic", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "mouse_move", "arguments": {"x": 1, "y": 1}}},
    ])
    panic = json.loads(responses[0]["result"]["content"][0]["text"])
    assert panic["panicked"] is True
    after = json.loads(responses[1]["result"]["content"][0]["text"])
    assert after["ok"] is False, after
