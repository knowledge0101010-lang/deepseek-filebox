#!/usr/bin/env python3
"""MCP (Model Context Protocol) stdio server exposing a DeepSeek Filebox.

Why this exists
---------------
ByteDance's Agent TARS / UI-TARS-desktop stack (Apache-2.0) is built on MCP: its kernel
speaks MCP and it mounts MCP servers to reach real-world tools, and UI-TARS Desktop ships
both local and **remote computer operators**. Exposing the filebox as an MCP server means a
TARS/UI-TARS agent can inspect and verify a consolidation run remotely with no custom glue.

Reference: https://github.com/bytedance/UI-TARS-desktop  (README: MCP Integration)
           https://agent-tars.com/guide/basic/mcp.html

Protocol
--------
MCP stdio transport: newline-delimited JSON-RPC 2.0 on stdin/stdout.
Implemented methods: `initialize`, `tools/list`, `tools/call`, plus `ping`.
Standard library only — no SDK dependency, consistent with the rest of this repo.

usage:
  python tools/filebox_mcp.py [--db PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness\DEEPSEEK-FILEBOX.db")
DB = DEFAULT_DB

TOOLS = [
    {
        "name": "filebox_stats",
        "description": "Summarize the filebox: entries, unique contents, sizes, duplicate "
                       "groups, and bytes avoided by not storing binaries.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "filebox_list",
        "description": "List indexed entries by size (descending).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "kind": {"type": "string", "enum": ["src", "src-big", "blob", "unreadable"]},
                "search": {"type": "string", "description": "substring of the source path"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "filebox_cat",
        "description": "Return the stored content of one source path (fails for binaries "
                       "and for entries whose content was not filled).",
        "inputSchema": {
            "type": "object",
            "properties": {"src": {"type": "string"}},
            "required": ["src"],
            "additionalProperties": False,
        },
    },
    {
        "name": "filebox_verify",
        "description": "End-to-end accuracy check: re-read originals and compare SHA-256 "
                       "against stored hashes. Returns checked/exact/mismatch/ratio.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}, "sample": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
    },
    {
        "name": "filebox_dedupe",
        "description": "Show the largest cross-duplicate groups (same SHA-256 under many paths).",
        "inputSchema": {
            "type": "object",
            "properties": {"top": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}},
            "additionalProperties": False,
        },
    },
]


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise RuntimeError(f"filebox not found: {DB}")
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)


def tool_stats(_args: dict | None = None) -> dict:
    con = connect()
    c = con.cursor()
    q = lambda s: c.execute(s).fetchone()[0]
    dup = c.execute(
        "SELECT COUNT(*), COALESCE(SUM(cnt-1),0) FROM ("
        "SELECT hash, COUNT(*) cnt FROM entries WHERE has_content=1 GROUP BY hash "
        "HAVING cnt>1)").fetchone()
    out = {
        "db": str(DB),
        "db_bytes": DB.stat().st_size,
        "entries": q("SELECT COUNT(*) FROM entries"),
        "with_content": q("SELECT COUNT(*) FROM entries WHERE has_content=1"),
        "unique_contents": q("SELECT COUNT(*) FROM contents"),
        "unique_bytes": q("SELECT COALESCE(SUM(size),0) FROM contents"),
        "blob_bytes_not_stored": q("SELECT COALESCE(SUM(size),0) FROM entries WHERE kind='blob'"),
        "total_bytes": q("SELECT COALESCE(SUM(size),0) FROM entries"),
        "duplicate_groups": dup[0],
        "duplicate_instances": dup[1] or 0,
    }
    con.close()
    return out


def tool_list(args: dict) -> dict:
    con = connect()
    q = "SELECT kind, size, has_content, src FROM entries WHERE 1=1"
    p: list = []
    for col, key in (("tag", "tag"), ("kind", "kind")):
        if args.get(key):
            q += f" AND {col}=?"
            p.append(args[key])
    if args.get("search"):
        q += " AND src LIKE ?"
        p.append(f"%{args['search']}%")
    q += " ORDER BY size DESC LIMIT ?"
    p.append(int(args.get("limit", 20)))
    rows = [{"kind": k, "size": s, "has_content": bool(h), "src": src}
            for k, s, h, src in con.execute(q, p)]
    con.close()
    return {"count": len(rows), "entries": rows}


def tool_cat(args: dict) -> dict:
    con = connect()
    row = con.execute(
        "SELECT c.data FROM entries e JOIN contents c ON c.hash=e.hash WHERE e.src=?",
        (args["src"],)).fetchone()
    con.close()
    if not row or row[0] is None:
        return {"found": False, "reason": "no stored content (binary or not filled)"}
    try:
        text = row[0].decode("utf-8")
    except UnicodeDecodeError:
        return {"found": True, "encoding": "binary", "bytes": len(row[0])}
    return {"found": True, "encoding": "utf-8", "text": text}


def tool_verify(args: dict) -> dict:
    import hashlib
    con = connect()
    limit = int(args.get("limit") or args.get("sample") or 0)
    sql = "SELECT src, hash, size FROM entries WHERE has_content=1 AND hash IS NOT NULL"
    if limit:
        sql += f" LIMIT {limit}"
    ok = mismatch = missing = 0
    for src, stored, size in con.execute(sql):
        try:
            data = Path(src).read_bytes()
        except OSError:
            missing += 1
            continue
        if hashlib.sha256(data).hexdigest() == stored and len(data) == size:
            ok += 1
        else:
            mismatch += 1
    con.close()
    total = ok + mismatch + missing
    return {"checked": total, "exact": ok, "mismatch": mismatch,
            "source_unreadable": missing, "exact_ratio": round(ok / total, 6) if total else None}


def tool_dedupe(args: dict) -> dict:
    con = connect()
    rows = con.execute(
        "SELECT hash, COUNT(*) cnt, MAX(size) size, MIN(src) sample FROM entries "
        "WHERE has_content=1 GROUP BY hash HAVING cnt>1 "
        "ORDER BY (cnt-1)*MAX(size) DESC LIMIT ?", (int(args.get("top", 10)),)).fetchall()
    con.close()
    return {"groups": [{"hash": h, "instances": n, "size": s, "sample": src}
                       for h, n, s, src in rows]}


HANDLERS = {
    "filebox_stats": (tool_stats, None),
    "filebox_list": (tool_list, "list"),
    "filebox_cat": (tool_cat, "cat"),
    "filebox_verify": (tool_verify, "verify"),
    "filebox_dedupe": (tool_dedupe, "dedupe"),
}


def dispatch(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "deepseek-filebox", "version": "0.1.0"},
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        fn, _ = HANDLERS[name]
        try:
            payload = fn(args)
        except Exception as exc:  # noqa: BLE001 - MCP expects an error result, not a crash
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]}}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    global DB
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    DB = Path(args.db)

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
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
