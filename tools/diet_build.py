r"""DIET 빌더 — 중복을 걷어낸 정본 코드와 전체 파일함을 **한곳**에 모은다.

다이어트 규칙
  1. 내용해시가 같은 파일은 **대표 경로 1개만** 물질화한다.
  2. 대표 경로는 "복사본/중복 사슬" 표시가 적고, 경로가 짧고 얕은 쪽을 고른다
     (예: `...\\deepseek\\deepseek\\deepseek\\Code-main\\...` 보다 `Code-main\\...`).
  3. 나머지 경로는 버리지 않고 MAP.json 에 남긴다(복원 가능).
  4. 파일함 DB 는 **하드링크**로 같은 곳에 둔다(바이트 중복 없이 "한곳").

출력
  DIET\
    ├─ src\<대표 상대경로>            고유 코드(중복 제거 후)
    ├─ MAP.json                       hash -> 모든 원본 경로
    ├─ DEEPSEEK-FILEBOX.db            전체 색인(하드링크)
    ├─ DIET-REPORT.md                 절감 수치
    └─ diet-manifest.json

usage: python diet_build.py [--out <dir>]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HARNESS = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness")
DB = HARNESS / "DEEPSEEK-FILEBOX.db"
DIET = HARNESS / "DIET"
NOISE = ("복사본", "__dup", " - copy", "(copy)")


def path_score(rel: str, tag: str) -> tuple:
    """대표 경로 점수(작을수록 좋다)."""
    low = rel.lower()
    noise_hits = sum(low.count(n.lower()) for n in NOISE)
    deep_chain = low.count("deepseek\\deepseek") + low.count("deepseek/deepseek")
    return (noise_hits, deep_chain, rel.count("\\") + rel.count("/"), len(rel), tag)


def link_or_copy(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "exists"
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        try:
            subprocess.run(["cmd", "/c", "mklink", "/H", str(dst), str(src)],
                           capture_output=True, check=False)
            if dst.exists():
                return "hardlink"
        except Exception:
            pass
        import shutil
        shutil.copy2(src, dst)
        return "copy"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DIET))
    args = ap.parse_args()

    if not DB.exists():
        raise SystemExit(f"파일함 없음: {DB}")

    t0 = time.time()
    out = Path(args.out)
    src_root = out / "src"
    src_root.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    # hash -> 후보 경로들
    by_hash: dict[str, list[tuple[str, str, str]]] = {}
    for h, src, rel, tag in con.execute(
            "SELECT hash, src, rel, tag FROM entries WHERE hash IS NOT NULL"):
        by_hash.setdefault(h, []).append((src, rel, tag))

    contents = con.execute("SELECT hash, size, data FROM contents").fetchall()
    con.close()

    written = reused = 0
    bytes_written = 0
    mapping = []
    for h, size, data in contents:
        if data is None:
            continue
        cands = by_hash.get(h, [])
        rep = min(cands, key=lambda c: path_score(c[1], c[2])) if cands else None
        obj = None
        if rep is not None:
            _, rel, tag = rep
            dst = src_root / tag / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    dst.write_bytes(data)
                    written += 1
                    bytes_written += size or 0
                else:
                    reused += 1
                obj = str(dst.relative_to(out))
            except OSError:
                obj = None
        mapping.append({
            "hash": h,
            "size": size,
            "object": obj,
            "representative": rep[0] if rep else None,
            "all_paths": [c[0] for c in cands],
        })

    (out / "MAP.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    # 파일함을 같은 곳에(하드링크 우선 → 바이트 중복 없이 "한곳")
    box_dst = out / DB.name
    mode = link_or_copy(DB, box_dst)

    # 인스턴스 총량(모든 경로) vs 고유 총량(DB 기준) — 다이어트의 핵심 수치
    instance_bytes = sum((m["size"] or 0) * len(m["all_paths"]) for m in mapping)
    unique_bytes_db = sum((s or 0) for _, s, _ in contents)
    dup_removed = instance_bytes - unique_bytes_db
    tree_files = sum(1 for _ in src_root.rglob("*") if _.is_file())
    tree_bytes = sum(p.stat().st_size for p in src_root.rglob("*") if p.is_file())

    report = {
        "out": str(out),
        "unique_objects": len([m for m in mapping if m["object"]]),
        "all_paths_mapped": sum(len(m["all_paths"]) for m in mapping),
        "unique_bytes": unique_bytes_db,
        "original_source_bytes": instance_bytes,
        "duplicate_bytes_removed": dup_removed,
        "diet_tree_files": tree_files,
        "diet_tree_bytes": tree_bytes,
        "filebox": {"path": str(box_dst), "link_mode": mode,
                    "db_bytes": box_dst.stat().st_size if box_dst.exists() else 0},
        "elapsed_s": round(time.time() - t0, 1),
    }
    (out / "diet-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                            encoding="utf-8")

    md = [
        "# DIET — 중복 제거 정본 + 전체 파일함 (한곳)", "",
        f"- 정본 객체: **{report['unique_objects']:,}개 / {bytes_written/1024/1024:.2f} MB**",
        f"- 매핑된 원본 경로: **{report['all_paths_mapped']:,}개**",
        f"- 제거된 중복 바이트: **{dup_removed/1024/1024:.2f} MB**",
        f"- 파일함 DB: `{box_dst}` (링크 모드 `{mode}`)",
        f"- 트리 크기: {tree_files:,}개 파일 / {tree_bytes/1024/1024:.2f} MB",
        f"- 소요: {report['elapsed_s']}s", "",
        "## 다이어트 규칙",
        "1. 같은 내용은 대표 경로 1개만 저장한다.",
        "2. 대표는 '복사본/중복 사슬' 표시가 적고 경로가 짧은 쪽을 고른다.",
        "3. 나머지 경로는 MAP.json 에 남겨 복원 가능하게 한다.",
        "4. 파일함 DB 는 하드링크로 같은 곳에 둔다(바이트 중복 0).",
    ]
    (out / "DIET-REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"리포트: {out/'DIET-REPORT.md'}  매핑: {out/'MAP.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
