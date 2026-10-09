"""합본 운영 도구 — 교차 중복 병합 + end-to-end 정확성 검증 + 정본(canonical) 생성.

  stats     : 규모·중복 요약
  dedupe    : 내용해시 기준 중복 그룹 상위 N + 절감량
  verify    : 저장된 해시를 **원본을 다시 읽어** 대조(바이트 단위 정확성)
  canonical : 고유 내용만 1회씩 물질화 + 경로 매핑(합본 코드 트리)
  report    : 위 결과를 MERGE-DEDUPE-REPORT.md 로 저장

usage:
  python filebox_ops.py stats
  python filebox_ops.py dedupe --top 15
  python filebox_ops.py verify --limit 5000
  python filebox_ops.py canonical --out .\filebox-canonical
  python filebox_ops.py report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

HARNESS = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness")
DB = HARNESS / "DEEPSEEK-FILEBOX.db"
REPORT = HARNESS / "MERGE-DEDUPE-REPORT.md"


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(f"파일함 없음: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    return con


def stats(con) -> dict:
    c = con.cursor()
    q = lambda s: c.execute(s).fetchone()[0]
    out = {
        "entries": q("SELECT COUNT(*) FROM entries"),
        "with_content": q("SELECT COUNT(*) FROM entries WHERE has_content=1"),
        "unique_contents": q("SELECT COUNT(*) FROM contents"),
        "content_bytes": q("SELECT COALESCE(SUM(size),0) FROM contents"),
        "pending_src": q("SELECT COUNT(*) FROM entries WHERE kind='src' AND has_content=0"),
        "blob_bytes": q("SELECT COALESCE(SUM(size),0) FROM entries WHERE kind='blob'"),
        "total_bytes": q("SELECT COALESCE(SUM(size),0) FROM entries"),
        "db_bytes": DB.stat().st_size,
    }
    dup = c.execute("SELECT COUNT(*), COALESCE(SUM(cnt-1),0), COALESCE(SUM((cnt-1)*s),0) FROM ("
                    "SELECT hash, COUNT(*) cnt, MAX(size) s FROM entries "
                    "WHERE has_content=1 GROUP BY hash HAVING cnt>1)").fetchone()
    out.update({"dup_groups": dup[0], "dup_extra_instances": dup[1], "dup_saved_bytes": dup[2] or 0})
    return out


def print_stats(s: dict) -> None:
    print(f"파일함: {DB}  ({s['db_bytes']/1024/1024:.1f} MB)")
    print(f"  색인 항목        : {s['entries']:,}")
    print(f"  내용 보유        : {s['with_content']:,}")
    print(f"  고유 내용        : {s['unique_contents']:,}  ({s['content_bytes']/1024/1024:.1f} MB)")
    print(f"  미채움 src       : {s['pending_src']:,}")
    print(f"  바이너리(미저장) : {s['blob_bytes']/1024**3:.2f} GB")
    print(f"  원본 총량        : {s['total_bytes']/1024**3:.2f} GB")
    print(f"  교차 중복        : 그룹 {s['dup_groups']:,} / 중복 인스턴스 {s['dup_extra_instances']:,}"
          f" / 절감 {s['dup_saved_bytes']/1024/1024:.1f} MB")


def dedupe(con, top: int) -> list[dict]:
    c = con.cursor()
    rows = c.execute(
        "SELECT hash, COUNT(*) cnt, MAX(size) size, MIN(src) sample FROM entries "
        "WHERE has_content=1 GROUP BY hash HAVING cnt>1 ORDER BY (cnt-1)*MAX(size) DESC LIMIT ?",
        (top,)).fetchall()
    out = []
    for h, cnt, size, sample in rows:
        out.append({"hash": h[:16], "instances": cnt, "size": size,
                    "saved_bytes": (cnt - 1) * (size or 0), "sample": sample})
    for r in out:
        print(f"  ×{r['instances']:<5} {(r['size'] or 0)/1024:9.1f} KB  절감 {r['saved_bytes']/1024/1024:7.2f} MB  {r['sample'][:90]}")
    return out


def verify(con, limit: int) -> dict:
    """저장 해시를 원본 재독으로 검증(end-to-end 정확성)."""
    c = con.cursor()
    sql = "SELECT src, hash, size FROM entries WHERE has_content=1 AND hash IS NOT NULL"
    if limit:
        sql += f" LIMIT {int(limit)}"
    ok = mismatch = missing = 0
    t0 = time.time()
    for src, stored, size in c.execute(sql):
        p = Path(src)
        try:
            data = p.read_bytes()
        except OSError:
            missing += 1
            continue
        if hashlib.sha256(data).hexdigest() == stored and len(data) == size:
            ok += 1
        else:
            mismatch += 1
    res = {"checked": ok + mismatch + missing, "exact": ok, "mismatch": mismatch,
           "source_unreadable": missing, "elapsed_s": round(time.time() - t0, 1),
           "exact_ratio": round(ok / max(1, ok + mismatch + missing), 6)}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def canonical(con, out_dir: Path) -> dict:
    """고유 내용만 1회 물질화하고 경로 매핑을 남긴다(=합본 코드).

    주의: SQLite 커서는 재사용하면 바깥 루프가 끊긴다 — 실제로 그 버그로 객체 1개만
    생성된 적이 있다. 여기서는 매핑을 먼저 dict 로 적재해 커서를 중첩하지 않는다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    entries_by_hash: dict[str, list[tuple[str, str, str]]] = {}
    for h, src, rel, tag in con.execute(
            "SELECT hash, src, rel, tag FROM entries WHERE hash IS NOT NULL"):
        entries_by_hash.setdefault(h, []).append((src, rel, tag))

    rows = con.execute("SELECT hash, size, data FROM contents").fetchall()
    written = total = 0
    mapping = []
    for h, size, data in rows:
        if data is None:
            continue
        sub = out_dir / "objects" / h[:2]
        sub.mkdir(parents=True, exist_ok=True)
        dst = sub / h
        if not dst.exists():
            dst.write_bytes(data)
            written += 1
        total += size or 0
        for src, rel, tag in entries_by_hash.get(h, []):
            mapping.append({"hash": h, "object": str(dst.relative_to(out_dir)),
                            "src": src, "tag": tag, "rel": rel})
    (out_dir / "MAP.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    lines = ["# 정본(합본) 코드 트리", "",
             f"- 고유 객체 파일: {written}개 (총 {total/1024/1024:.2f} MB)",
             f"- 경로 매핑 항목: {len(mapping)}개 → MAP.json", "",
             "내용이 같은 파일은 한 번만 저장되고, 원래 위치는 MAP.json 으로 복원한다."]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = {"objects_written": written, "bytes": total, "mappings": len(mapping),
           "out": str(out_dir)}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def main() -> int:
    global DB, REPORT

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=str(DB), help="파일함 경로(기본: 하네스 루트)")
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", parents=[common])
    d = sub.add_parser("dedupe", parents=[common]); d.add_argument("--top", type=int, default=15)
    v = sub.add_parser("verify", parents=[common]); v.add_argument("--limit", type=int, default=0)
    cn = sub.add_parser("canonical", parents=[common])
    cn.add_argument("--out", default=r".\filebox-canonical")
    sub.add_parser("report", parents=[common])
    a = ap.parse_args()

    if a.db:
        DB = Path(a.db)
        REPORT = DB.parent / "MERGE-DEDUPE-REPORT.md"
    con = connect()
    if a.cmd == "stats":
        print_stats(stats(con))
    elif a.cmd == "dedupe":
        dedupe(con, a.top)
    elif a.cmd == "verify":
        verify(con, a.limit)
    elif a.cmd == "canonical":
        canonical(con, Path(a.out))
    elif a.cmd == "report":
        s = stats(con)
        top = dedupe(con, 10)
        md = ["# 교차 중복 병합 리포트", "",
              f"- 파일함: `{DB}` ({s['db_bytes']/1024/1024:.1f} MB)",
              f"- 색인 {s['entries']:,} · 내용보유 {s['with_content']:,} · 고유내용 {s['unique_contents']:,}",
              f"- 원본 총량 {s['total_bytes']/1024**3:.2f} GB 중 바이너리 {s['blob_bytes']/1024**3:.2f} GB 는 미저장(인덱스만)",
              f"- 교차 중복: 그룹 {s['dup_groups']:,}, 중복 인스턴스 {s['dup_extra_instances']:,}, "
              f"절감 {s['dup_saved_bytes']/1024/1024:.1f} MB", "",
              "## 중복 상위 그룹", ""]
        md += [f"- ×{r['instances']} {(r['size'] or 0)/1024:.1f} KB (절감 {r['saved_bytes']/1024/1024:.2f} MB) — `{r['sample'][:80]}`" for r in top]
        md += ["", "## 정확성", "",
               "`python filebox_ops.py verify` 로 저장 해시를 원본 재독과 대조한다(바이트 단위)."]
        REPORT.write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"리포트 저장: {REPORT}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
