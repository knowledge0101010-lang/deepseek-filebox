#!/usr/bin/env python3
"""Box self-consistency check — re-hash the content stored in the filebox.

`filebox_ops.py verify` re-reads the ORIGINALS. That is the strongest check, but it is only
possible for files that still exist at their indexed paths: in the measured run 131,838 of
155,006 originals had been moved or deleted after indexing (their content is still in the
box). This tool covers exactly that gap: it re-hashes every stored object and compares it
with the hash key that identifies it.

usage:
  python tools/box_selfcheck.py [--db PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness\DEEPSEEK-FILEBOX.db")
DB = DEFAULT_DB


def main() -> int:
    global DB
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    DB = Path(args.db)
    if not DB.exists():
        raise SystemExit(f"filebox not found: {DB}")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    sql = "SELECT hash, size, data FROM contents"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    t0 = time.time()
    ok = mismatch = null = 0
    bad: list[str] = []
    for h, size, data in con.execute(sql):
        if data is None:
            null += 1
            continue
        if hashlib.sha256(data).hexdigest() == h and len(data) == size:
            ok += 1
        else:
            mismatch += 1
            if len(bad) < 5:
                bad.append(h)
    con.close()
    result = {
        "db": str(DB),
        "checked_objects": ok + mismatch + null,
        "exact": ok,
        "mismatch": mismatch,
        "null_content": null,
        "exact_ratio": round(ok / max(1, ok + mismatch), 6),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 1 if mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
