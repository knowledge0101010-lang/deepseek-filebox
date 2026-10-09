r"""자동 정밀 파이프라인 — 남은 채우기부터 검증·축소·정본·리포트까지 무인 실행.

단계
  1) fill  : filebox_build.py --phase fill 를 pending=0 이 될 때까지 반복(재개 가능)
  2) verify: 저장 해시를 원본 재독 sha256 으로 전수 대조(정확성)
  3) e2e   : 정규화 동등 + 근접 복제 검출(dedupe_e2e.py)
  4) diet  : 중복 제거 정본 트리 재빌드(diet_build.py)
  5) report: FINAL-PRECISION-REPORT.md 생성

로그: DIET/FINALIZE.log
usage: python auto_finalize.py [--max-rounds 200]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HARNESS = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness")
DB = HARNESS / "DEEPSEEK-FILEBOX.db"
DIET = HARNESS / "DIET"
TOOLS = DIET / "tools"
LOG = DIET / "FINALIZE.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pending() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    n = con.execute("SELECT COUNT(*) FROM entries WHERE kind='src' AND has_content=0").fetchone()[0]
    con.close()
    return n


def run(args: list[str], timeout: int = 7200) -> str:
    p = subprocess.run([sys.executable, *args], cwd=str(TOOLS), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-rounds", type=int, default=400)
    args = ap.parse_args()

    log("=== 자동 정밀 파이프라인 시작 ===")
    remaining = pending()
    log(f"미채움 src: {remaining:,}")

    for r in range(1, args.max_rounds + 1):
        if remaining <= 0:
            break
        out = run(["filebox_build.py", "--phase", "fill", "--max-mb", "8"])
        m = re.search(r"내용 채우기 대상: (\d+)개", out)
        log(f"  라운드 {r}: {out.strip().splitlines()[-1] if out.strip() else 'no output'}")
        remaining = pending()
        log(f"  남은 미채움: {remaining:,}")
        if m and int(m.group(1)) == 0:
            break

    log("--- 2) 정확성 전수 검증 ---")
    ver = run(["filebox_ops.py", "verify"])
    log(ver.strip().replace("\n", " ")[:400])

    log("--- 3) E2E 축소 분석 ---")
    e2e = run(["dedupe_e2e.py"])
    log(e2e.strip().replace("\n", " ")[:400])

    log("--- 4) 정본 트리 재빌드 ---")
    diet = run(["diet_build.py"])
    log(diet.strip().replace("\n", " ")[:600])

    log("--- 5) 최종 리포트 ---")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    c = con.cursor()
    q = lambda s: c.execute(s).fetchone()[0]
    stats = {
        "entries": q("SELECT COUNT(*) FROM entries"),
        "with_content": q("SELECT COUNT(*) FROM entries WHERE has_content=1"),
        "unique_contents": q("SELECT COUNT(*) FROM contents"),
        "unique_bytes": q("SELECT COALESCE(SUM(size),0) FROM contents"),
        "blob_bytes": q("SELECT COALESCE(SUM(size),0) FROM entries WHERE kind='blob'"),
        "total_bytes": q("SELECT COALESCE(SUM(size),0) FROM entries"),
        "pending_src": q("SELECT COUNT(*) FROM entries WHERE kind='src' AND has_content=0"),
    }
    con.close()
    diet_manifest = json.loads((DIET / "diet-manifest.json").read_text(encoding="utf-8")) \
        if (DIET / "diet-manifest.json").exists() else {}
    ver_json = json.loads(ver[ver.find("{"):ver.rfind("}") + 1]) if "{" in ver else {}
    cluster = json.loads((DIET / "e2e-clusters.json").read_text(encoding="utf-8")) \
        if (DIET / "e2e-clusters.json").exists() else {}

    md = [
        "# FINAL PRECISION REPORT — 자동 정밀 파이프라인", "",
        f"- 생성: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 파일함: `{DB}`", "",
        "## 규모 (A: 바이트 동일 중복 제거)", "",
        f"- 색인 항목 **{stats['entries']:,}** · 내용 보유 {stats['with_content']:,} · 고유 내용 **{stats['unique_contents']:,}**",
        f"- 고유 용량 **{stats['unique_bytes']/1024/1024:.2f} MB** / 원본 총량 {stats['total_bytes']/1024**3:.2f} GB",
        f"- 바이너리 미저장 {stats['blob_bytes']/1024**3:.2f} GB",
        f"- 미채움 src {stats['pending_src']:,}", "",
        "## 정확성 (end-to-end 원본 재독 검증)", "",
        f"- 검증 {ver_json.get('checked', '?')}건 · 정확 {ver_json.get('exact', '?')} · 불일치 {ver_json.get('mismatch', '?')} · 정확도 **{ver_json.get('exact_ratio', '?')}**", "",
        "## B/C: 정규화 동등 · 근접 복제", "",
        f"- 정규화 동등 그룹 {cluster.get('clusters', {}).get('normalized_equivalence_groups', '?')}",
        f"- 근접 복제 쌍(≥0.90) {cluster.get('clusters', {}).get('near_duplicate_pairs_ge_0.90', '?')}", "",
        "## DIET 정본 트리", "",
        f"- 고유 객체 {diet_manifest.get('unique_objects', '?')} / {diet_manifest.get('unique_bytes', 0)/1024/1024:.2f} MB",
        f"- 매핑 경로 {diet_manifest.get('all_paths_mapped', '?')}",
        f"- 제거된 중복 바이트 {diet_manifest.get('duplicate_bytes_removed', 0)/1024/1024:.2f} MB", "",
        "## 정확성 규칙", "",
        "1. A(바이트 동일)만 실제로 합친다.",
        "2. B(정규화 동등)는 보고만 — 원본 바이트 보존.",
        "3. C(근접 복제)는 절대 병합하지 않는다.",
    ]
    (DIET / "FINAL-PRECISION-REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"최종 리포트: {DIET/'FINAL-PRECISION-REPORT.md'}")
    log("=== 파이프라인 종료 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
