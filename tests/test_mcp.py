"""MCP server tests — the ByteDance UI-TARS / Agent TARS integration path.

Verifies the stdio JSON-RPC surface: initialize, tools/list, and tools/call for the
read-only filebox tools. Runs against a temporary fixture; no network, no Node.js.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def rpc(db: Path, requests: list[dict]) -> list[dict]:
    payload = "\n".join(json.dumps(r) for r in requests) + "\n"
    proc = subprocess.run([sys.executable, str(TOOLS / "filebox_mcp.py"), "--db", str(db)],
                          input=payload, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def build_fixture(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "pkg" / "app_copy.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "pkg" / "other.py").write_text("value = 42\n", encoding="utf-8")
    (src / "pkg" / "blob.bin").write_bytes(b"\x00\x01" * 100)
    db = tmp_path / "box.db"
    proc = subprocess.run([sys.executable, str(TOOLS / "filebox_build.py"),
                           "--phase", "all", "--sources", f"{src}=test", "--db", str(db)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return db


def test_mcp_initialize_and_tools_list(tmp_path):
    db = build_fixture(tmp_path)
    responses = rpc(db, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},          # no reply expected
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    assert len(responses) == 2, responses                                  # notification not answered
    init = responses[0]["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert init["serverInfo"]["name"] == "deepseek-filebox"

    names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert names == {"filebox_stats", "filebox_list", "filebox_cat",
                     "filebox_verify", "filebox_dedupe"}, names


def test_mcp_tools_call_stats_list_verify(tmp_path):
    db = build_fixture(tmp_path)
    responses = rpc(db, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "filebox_stats", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "filebox_list", "arguments": {"limit": 5}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "filebox_verify", "arguments": {"limit": 50}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "filebox_cat", "arguments": {"src": str(tmp_path / "src" / "pkg" / "app.py")}}},
    ])

    def payload(r):
        return json.loads(r["result"]["content"][0]["text"])

    stats = payload(responses[0])
    assert stats["entries"] == 4, stats
    # 3 entries have content (app.py, app_copy.py, other.py) but only 2 unique objects:
    # app.py and app_copy.py are byte-identical. blob.bin is indexed as a fingerprint only.
    assert stats["unique_contents"] == 2, stats
    assert stats["duplicate_instances"] == 1, stats

    listing = payload(responses[1])
    assert listing["count"] == 4 and any(e["src"].endswith("app.py") for e in listing["entries"])

    verify = payload(responses[2])
    assert verify["mismatch"] == 0, verify
    assert verify["exact_ratio"] == 1.0, verify

    cat = payload(responses[3])
    assert cat["found"] is True and "print('hello')" in cat["text"]


def test_mcp_unknown_tool_is_an_error(tmp_path):
    db = build_fixture(tmp_path)
    responses = rpc(db, [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "does/not/exist"},
    ])
    assert responses[0]["error"]["code"] == -32602
    assert responses[1]["error"]["code"] == -32601
