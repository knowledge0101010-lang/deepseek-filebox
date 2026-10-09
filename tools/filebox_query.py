"""Read-only CLI over a DeepSeek Filebox database.

usage:
  python filebox_query.py stats [--db PATH]
  python filebox_query.py list [--tag TAG] [--kind KIND] [--search TEXT] [--limit N]
  python filebox_query.py blobs [--limit N]
  python filebox_query.py cat "<source path>"
  python filebox_query.py extract <out_dir>
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness\DEEPSEEK-FILEBOX.db")
DB = DEFAULT_DB


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(f"filebox not found: {DB}  (run filebox_build.py first)")
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)


def cmd_stats(_a) -> int:
    con = connect()
    c = con.cursor()
    print(f"filebox : {DB}  ({DB.stat().st_size/1024/1024:.1f} MB)")
    print(f"entries : {c.execute('SELECT COUNT(*) FROM entries').fetchone()[0]:,}")
    print(f"stored  : {c.execute('SELECT COUNT(*) FROM entries WHERE has_content=1').fetchone()[0]:,}")
    print(f"unique  : {c.execute('SELECT COUNT(*) FROM contents').fetchone()[0]:,}"
          f"  ({(c.execute('SELECT COALESCE(SUM(size),0) FROM contents').fetchone()[0])/1024/1024:.2f} MB)")
    print("\nby kind:")
    for k, n, s in c.execute("SELECT kind, COUNT(*), SUM(size) FROM entries GROUP BY kind ORDER BY 2 DESC"):
        print(f"  {k:10s} {n:9,d}  {(s or 0)/1024/1024:12.1f} MB")
    print("\nby tag:")
    for k, n, s in c.execute("SELECT tag, COUNT(*), SUM(size) FROM entries GROUP BY tag ORDER BY 2 DESC"):
        print(f"  {k:20s} {n:9,d}  {(s or 0)/1024/1024:12.1f} MB")
    return 0


def cmd_list(a) -> int:
    con = connect()
    q = "SELECT kind, size, has_content, src FROM entries WHERE 1=1"
    p: list = []
    for col, val in (("tag", a.tag), ("kind", a.kind)):
        if val:
            q += f" AND {col}=?"
            p.append(val)
    if a.search:
        q += " AND src LIKE ?"
        p.append(f"%{a.search}%")
    q += " ORDER BY size DESC LIMIT ?"
    p.append(a.limit)
    for kind, size, has, src in con.execute(q, p):
        print(f"{kind:8s} {(size or 0)/1024:10.1f} KB  content={'Y' if has else 'n'}  {src}")
    return 0


def cmd_blobs(a) -> int:
    con = connect()
    for kind, size, src in con.execute(
            "SELECT kind, size, src FROM entries WHERE kind IN ('blob','src-big') "
            "ORDER BY size DESC LIMIT ?", (a.limit,)):
        print(f"{(size or 0)/1024**3:8.2f} GB  {kind:8s} {src}")
    return 0


def cmd_cat(a) -> int:
    con = connect()
    row = con.execute(
        "SELECT c.data FROM entries e JOIN contents c ON c.hash=e.hash WHERE e.src=?",
        (a.src,)).fetchone()
    if not row or row[0] is None:
        print("no stored content for that path (binary or not filled yet)", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(row[0])
    return 0


def cmd_extract(a) -> int:
    con = connect()
    out = Path(a.out_dir)
    n = 0
    for tag, rel, data in con.execute(
            "SELECT e.tag, e.rel, c.data FROM entries e JOIN contents c ON c.hash=e.hash"):
        if data is None:
            continue
        dst = out / tag / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        n += 1
    print(f"restored {n} files -> {out}")
    return 0


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(DEFAULT_DB), help="filebox path")
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", parents=[common]).set_defaults(func=cmd_stats)
    lp = sub.add_parser("list", parents=[common])
    lp.add_argument("--tag"); lp.add_argument("--kind"); lp.add_argument("--search")
    lp.add_argument("--limit", type=int, default=20)
    lp.set_defaults(func=cmd_list)
    bp = sub.add_parser("blobs", parents=[common])
    bp.add_argument("--limit", type=int, default=20)
    bp.set_defaults(func=cmd_blobs)
    cp = sub.add_parser("cat", parents=[common]); cp.add_argument("src")
    cp.set_defaults(func=cmd_cat)
    ep = sub.add_parser("extract", parents=[common]); ep.add_argument("out_dir")
    ep.set_defaults(func=cmd_extract)
    a = ap.parse_args()

    global DB
    DB = Path(a.db)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
