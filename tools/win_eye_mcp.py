#!/usr/bin/env python3
r"""win_eye MCP server — expose read-only Windows OS observation over MCP.

Mounted by an MCP client (e.g. Agent TARS / UI-TARS Desktop, or DSH), this gives an agent
the SYSTEM layer of the machine: processes, services, disks, network, event log, startup
items, installed software, sessions, environment — alongside the screen/window/file layers
served by `operator_mcp.py`, `window_ctl.py` and `filebox_mcp.py`.

Everything is READ-ONLY: nothing is started, stopped, killed or written.

usage:
  python tools/win_eye_mcp.py            # stdio JSON-RPC 2.0
  python tools/win_eye_mcp.py --pretty   # pretty-print responses (debugging)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_win_eye():
    spec = importlib.util.spec_from_file_location("win_eye", HERE / "win_eye.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["win_eye"] = mod
    spec.loader.exec_module(mod)
    return mod


TOOLS = [
    {"name": "win_overview",
     "description": "Host snapshot: OS build, uptime, CPU load, RAM, process count, GPU.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "win_processes",
     "description": "Top processes by CPU seconds or memory.",
     "inputSchema": {"type": "object",
                     "properties": {"top": {"type": "integer", "minimum": 1, "maximum": 100},
                                    "sort": {"type": "string", "enum": ["cpu", "mem"]}},
                     "additionalProperties": False}},
    {"name": "win_services",
     "description": "Windows services, optionally filtered by state.",
     "inputSchema": {"type": "object",
                     "properties": {"state": {"type": "string",
                                              "enum": ["Running", "Stopped", "Paused"]},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                     "additionalProperties": False}},
    {"name": "win_disk",
     "description": "Fixed volumes with size, free space and free percentage.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "win_network",
     "description": "Active adapters, listening ports and established TCP connections.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "win_events",
     "description": "Recent System/Application event-log entries at a given level.",
     "inputSchema": {"type": "object",
                     "properties": {"level": {"type": "string",
                                              "enum": ["critical", "error", "warning",
                                                       "information"]},
                                    "last": {"type": "integer", "minimum": 1, "maximum": 200}},
                     "additionalProperties": False}},
    {"name": "win_startup",
     "description": "Autostart entries: Run registry keys and the Startup folder.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "win_software",
     "description": "Installed programs (display name, version, publisher).",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
                     "additionalProperties": False}},
    {"name": "win_env",
     "description": "User, home, temp, culture, timezone, PowerShell version, admin flag.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "win_digest",
     "description": "Compact digest: overview + disk + top processes + recent errors.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]

WE = None


def call_tool(name: str, args: dict) -> dict:
    if name == "win_overview":
        return WE.overview()
    if name == "win_processes":
        return WE.processes(int(args.get("top", 15)), str(args.get("sort", "mem")))
    if name == "win_services":
        return WE.services(args.get("state"), int(args.get("limit", 20)))
    if name == "win_disk":
        return WE.disk()
    if name == "win_network":
        return WE.network()
    if name == "win_events":
        return WE.events(str(args.get("level", "error")), int(args.get("last", 15)))
    if name == "win_startup":
        return WE.startup()
    if name == "win_software":
        return WE.software(int(args.get("limit", 30)))
    if name == "win_env":
        return WE.env()
    if name == "win_digest":
        return WE.digest()
    raise KeyError(name)


def dispatch(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "deepseek-win-eye", "version": "0.1.0"}}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        try:
            payload = call_tool(name, params.get("arguments") or {})
        except KeyError:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        except Exception as exc:  # noqa: BLE001
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(payload, ensure_ascii=False)}]}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    global WE
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    WE = load_win_eye()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = dispatch(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False,
                                        indent=1 if args.pretty else None) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
