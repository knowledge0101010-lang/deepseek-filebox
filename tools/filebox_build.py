"""합본 빌더 v2 — 단일 SQLite 파일함을 2단계 증분으로 만든다.

1단계(index): 파일을 읽지 않고 이름·크기만 훑어 전체 색인을 만든다(빠름).
2단계(fill) : 소스 파일 내용을 읽어 해시 중복 제거 후 채운다(500개마다 커밋, 재개 가능).

복사 병합이 아니다. 트리에 파일을 만들지 않고 원본은 제자리에 둔다.
DB 경로는 하네스 루트: C:\\Users\\knowl\\OneDrive\\문서\\deepseek-harness\\DEEPSEEK-FILEBOX.db

usage:
  python filebox_build.py --phase index
  python filebox_build.py --phase fill [--limit N] [--max-mb 8]
  python filebox_build.py --phase stats
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

HARNESS = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness")
DOWNLOADS = Path(r"C:\Users\knowl\Downloads")
DB_PATH = HARNESS / "DEEPSEEK-FILEBOX.db"
INLINE_MAX = 8 * 1024 * 1024

SOURCE_EXTS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".jsonl",
    ".md", ".mdx", ".txt", ".rst", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".ps1", ".psm1", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".rs", ".go", ".java", ".kt", ".swift", ".html", ".htm",
    ".css", ".scss", ".sql", ".ipynb", ".proto", ".graphql", ".vue", ".svelte",
    ".lua", ".rb", ".php", ".pl", ".r", ".jl", ".cjo", ".dsil", ".xml", ".csv", ".tsv",
}
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv", ".next",
             ".cache", "dist", "build", ".gradle", ".mypy_cache", ".pytest_cache",
             "site-packages"}
SKIP_NAMES = {"DEEPSEEK-FILEBOX.db", "FILEBOX-REPORT.md", "filebox-progress.log",
              "merge-manifest.json", "merge-undo.ps1"}

PLAN = [
    (DOWNLOADS, "downloads"),
    (Path(r"C:\Users\knowl\.codex\Antigravity_All_In_One\.gemini\.antigravity-ide"), "antigravity-ide"),
    (Path(r"C:\Users\knowl\OneDrive\문서\Antigravity"), "antigravity-docs"),
    (Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness\Antigravity"), "antigravity-harness"),
    (Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness\default-workspace\Antigravity"),
     "antigravity-workspace"),
]

DDL = """
CREATE TABLE IF NOT EXISTS contents(hash TEXT PRIMARY KEY, size INTEGER, kind TEXT, data BLOB);
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY, src TEXT UNIQUE, tag TEXT, rel TEXT,
  kind TEXT, size INTEGER, hash TEXT, has_content INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_tag ON entries(tag);
CREATE INDEX IF NOT EXISTS idx_entries_fill ON entries(has_content, kind, size);
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(DDL)
    con.commit()
    return con


def iter_files(root: Path):
    if root.is_file():
        yield Path(root.name), root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_NAMES:
                continue
            ap = Path(dirpath) / fn
            yield ap.relative_to(root), ap


def classify(p: Path, size: int) -> str:
    ext = p.suffix.lower()
    if ext in SOURCE_EXTS or p.name.lower().startswith(".env"):
        return "src" if size <= INLINE_MAX else "src-big"
    return "blob"


def phase_index(con: sqlite3.Connection, limit: int, plan=None) -> int:
    cur = con.cursor()
    n = 0
    t0 = time.time()
    for root, tag in (plan or PLAN):
        if not root.exists():
            log(f"없음: {root}")
            continue
        log(f"색인: {root}")
        for rel, apath in iter_files(root):
            if limit and n >= limit:
                break
            try:
                size = apath.stat().st_size
            except OSError:
                continue
            cur.execute(
                "INSERT OR IGNORE INTO entries(src,tag,rel,kind,size) VALUES(?,?,?,?,?)",
                (str(apath), tag, str(rel), classify(apath, size), size))
            n += 1
            if n % 2000 == 0:
                con.commit()
                log(f"  {n}개 …")
        con.commit()
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('indexed_at',?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"),))
    con.commit()
    total = cur.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    log(f"색인 완료: 신규 {n}개, 총 {total}개, {time.time()-t0:.1f}s")
    return total


def phase_fill(con: sqlite3.Connection, limit: int, max_mb: float) -> int:
    cur = con.cursor()
    cap = int(max_mb * 1024 * 1024)
    rows = cur.execute(
        "SELECT id, src FROM entries WHERE kind='src' AND has_content=0 AND size<=? "
        "ORDER BY size ASC", (cap,)).fetchall()
    log(f"내용 채우기 대상: {len(rows)}개")
    done = dup = err = 0
    t0 = time.time()
    for eid, src in rows:
        if limit and done >= limit:
            break
        try:
            data = Path(src).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
        except OSError:
            err += 1
            cur.execute("UPDATE entries SET kind='unreadable' WHERE id=?", (eid,))
            continue
        if cur.execute("SELECT 1 FROM contents WHERE hash=?", (digest,)).fetchone():
            dup += 1
        else:
            cur.execute("INSERT OR IGNORE INTO contents(hash,size,kind,data) VALUES(?,?,?,?)",
                        (digest, len(data), "text", data))
        cur.execute("UPDATE entries SET hash=?, has_content=1 WHERE id=?", (digest, eid))
        done += 1
        if done % 500 == 0:
            con.commit()
            log(f"  {done}/{len(rows)} (중복 {dup}, 오류 {err}) …")
    con.commit()
    log(f"채우기 완료: {done}개 (중복 {dup}, 오류 {err}), {time.time()-t0:.1f}s")
    return done


def phase_stats(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    size_mb = DB_PATH.stat().st_size / 1024 / 1024 if DB_PATH.exists() else 0.0
    print(f"파일함: {DB_PATH}  ({size_mb:.1f} MB)")
    print(f"색인 항목 : {cur.execute('SELECT COUNT(*) FROM entries').fetchone()[0]}")
    print(f"내용 보유 : {cur.execute('SELECT COUNT(*) FROM entries WHERE has_content=1').fetchone()[0]}")
    print(f"고유 내용 : {cur.execute('SELECT COUNT(*) FROM contents').fetchone()[0]}"
          f"  ({(cur.execute('SELECT COALESCE(SUM(size),0) FROM contents').fetchone()[0])/1024/1024:.1f} MB)")
    print("\n분류별:")
    for k, c, s in cur.execute("SELECT kind, COUNT(*), SUM(size) FROM entries GROUP BY kind ORDER BY 2 DESC"):
        print(f"  {k:12s} {c:8d}개  {(s or 0)/1024/1024:12.1f} MB")
    print("\n태그별:")
    for k, c, s in cur.execute("SELECT tag, COUNT(*), SUM(size) FROM entries GROUP BY tag ORDER BY 2 DESC"):
        print(f"  {k:22s} {c:8d}개  {(s or 0)/1024/1024:12.1f} MB")
    pending = cur.execute(
        "SELECT COUNT(*) FROM entries WHERE kind='src' AND has_content=0").fetchone()[0]
    print(f"\n미완(내용 미채움 src): {pending}")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    global DB_PATH

    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["index", "fill", "stats", "all"], default="index")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-mb", type=float, default=8.0)
    ap.add_argument("--sources", default="",
                    help='"path=tag;path=tag" 형식. 주면 기본 PLAN 을 대체한다(테스트·이식용)')
    ap.add_argument("--db", default=str(DB_PATH), help="파일함 경로")
    args = ap.parse_args()

    DB_PATH = Path(args.db)
    plan = PLAN
    if args.sources:
        plan = []
        for item in args.sources.split(";"):
            item = item.strip()
            if not item:
                continue
            path, _, tag = item.partition("=")
            plan.append((Path(path), tag or "src"))

    HARNESS.mkdir(parents=True, exist_ok=True)
    con = connect()
    t0 = time.time()
    if args.phase == "index":
        phase_index(con, args.limit, plan)
    elif args.phase == "fill":
        phase_fill(con, args.limit, args.max_mb)
    elif args.phase == "all":
        phase_index(con, args.limit, plan)
        phase_fill(con, args.limit, args.max_mb)
    phase_stats(con)
    con.close()
    log(f"총 소요 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
