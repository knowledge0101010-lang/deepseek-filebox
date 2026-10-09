"""E2E 코드 축소 — "가장 좋은 부품은 부품이 없다"(정확성 우선).

3단계로 중복을 줄인다. **동등성이 증명된 것만** 합치고, 증명 못 한 유사는 보고만 한다.

  A. 바이트 동일      : sha256 동일 → 이미 파일함에서 1개로 접힘
  B. 정규화 동등      : BOM·줄바꿈(CRLF/CR/LF)·줄끝 공백·말미 공백줄만 다른 경우
                        → 정규화 해시가 같으면 "같은 코드"로 보고(바이트는 보존)
  C. 근접 복제(클론)  : 토큰 5-gram MinHash + LSH 로 후보를 뽑고 Jaccard>=0.9 만 보고
                        → 삭제하지 않고 클러스터로 제시(사람이 판단)

정확성 규칙
  * B 는 "정규화 동등"이지 바이트 동등이 아니다 → 원본 바이트는 파일함에 그대로 남는다.
  * C 는 절대 병합하지 않는다. 근거(유사도·크기)만 남긴다.

출력: DIET/E2E-DEDUPE-REPORT.md, DIET/e2e-clusters.json

usage: python dedupe_e2e.py [--min-bytes 1024] [--max-bytes 1048576] [--max-files 20000]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HARNESS = Path(r"C:\Users\knowl\OneDrive\문서\deepseek-harness")
DB = HARNESS / "DEEPSEEK-FILEBOX.db"
DIET = HARNESS / "DIET"
SHINGLE = 5
PERMS = 128
BANDS = 16


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)


def normalize_text(b: bytes) -> str | None:
    """텍스트로 해석되면 정규화 문자열을, 아니면 None(바이너리)."""
    if b"\x00" in b[:4096]:
        return None
    try:
        t = b.decode("utf-8")
    except UnicodeDecodeError:
        try:
            t = b.decode("latin-1")
        except Exception:
            return None
    if t.startswith("\ufeff"):
        t = t[1:]
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return t.rstrip("\n") + "\n"


def minhash(tokens: list[str], k: int = SHINGLE, perms: int = PERMS) -> list[int]:
    sh = set()
    for i in range(len(tokens) - k + 1):
        sh.add(hashlib.blake2b(" ".join(tokens[i:i + k]).encode(), digest_size=8).digest())
    sig = [10 ** 18] * perms
    for s in sh:
        hv = int.from_bytes(s, "big")
        for p in range(perms):
            v = (hv * (p * 2 + 1) + p * 7919) % (2 ** 61 - 1)
            if v < sig[p]:
                sig[p] = v
    return sig


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bytes", type=int, default=1024)
    ap.add_argument("--max-bytes", type=int, default=1024 * 1024)
    ap.add_argument("--max-files", type=int, default=20000)
    args = ap.parse_args()

    t0 = time.time()
    con = connect()
    rows = con.execute("SELECT hash, size, data FROM contents WHERE data IS NOT NULL").fetchall()
    # hash -> 대표 원본 경로 수
    counts = dict(con.execute(
        "SELECT hash, COUNT(*) FROM entries WHERE hash IS NOT NULL GROUP BY hash").fetchall())
    con.close()

    total_unique_bytes = sum(s or 0 for _, s, _ in rows)
    print(f"고유 내용 {len(rows):,}개 / {total_unique_bytes/1024/1024:.2f} MB 로 E2E 축소 시작")

    # ---- B. 정규화 동등 ----
    norm_groups: dict[str, list[tuple[str, int]]] = {}
    for h, size, data in rows:
        if data is None:
            continue
        t = normalize_text(data)
        if t is None:
            continue
        nh = hashlib.sha256(t.encode("utf-8", "replace")).hexdigest()
        norm_groups.setdefault(nh, []).append((h, size or 0))

    norm_dupe_groups = {k: v for k, v in norm_groups.items() if len({h for h, _ in v}) > 1}
    norm_saved = 0
    norm_examples = []
    for k, v in sorted(norm_dupe_groups.items(),
                       key=lambda kv: -sum(s for _, s in kv[1]))[:200]:
        hs = {h for h, _ in v}
        sizes = [s for h, s in v if h == min(hs)]
        keep = max(sizes) if sizes else 0
        extra = sum(s for _, s in v) - keep
        norm_saved += extra
        if len(norm_examples) < 10:
            norm_examples.append({"normalized": k[:16], "variants": len(hs),
                                  "bytes": sum(s for _, s in v), "saved": extra})

    # ---- C. 근접 복제 ----
    cand = [(h, s, d) for h, s, d in rows
            if args.min_bytes <= (s or 0) <= args.max_bytes]
    cand.sort(key=lambda x: -(x[1] or 0))
    cand = cand[:args.max_files]
    sigs = {}
    for h, s, d in cand:
        try:
            text = d.decode("utf-8")
        except UnicodeDecodeError:
            continue
        toks = re.findall(r"[A-Za-z0-9_]+", text.lower())
        if len(toks) < SHINGLE + 1:
            continue
        sigs[h] = (minhash(toks), s or 0)

    buckets: dict[tuple, list[str]] = {}
    for h, (sig, _) in sigs.items():
        for b in range(BANDS):
            key = (b, tuple(sig[b::BANDS][:PERMS // BANDS]))
            buckets.setdefault(key, []).append(h)

    pairs = set()
    for key, hs in buckets.items():
        if len(hs) < 2:
            continue
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                pairs.add((min(hs[i], hs[j]), max(hs[i], hs[j])))

    sig_pairs = []
    for a, b in pairs:
        sa, sb = sigs[a][0], sigs[b][0]
        same = sum(1 for x, y in zip(sa, sb) if x == y)
        jac = same / len(sa)
        if jac >= 0.9:
            sig_pairs.append({"a": a[:16], "b": b[:16], "jaccard": round(jac, 3),
                              "size_a": sigs[a][1], "size_b": sigs[b][1]})
    sig_pairs.sort(key=lambda r: -r["jaccard"])

    clusters = {"normalized_equivalence_groups": len(norm_dupe_groups),
                "normalized_extra_bytes": norm_saved,
                "near_duplicate_pairs_ge_0.90": len(sig_pairs),
                "files_considered_for_clone_detection": len(sigs)}

    (DIET / "e2e-clusters.json").write_text(
        json.dumps({"clusters": clusters, "normalized_examples": norm_examples,
                    "near_duplicate_pairs": sig_pairs[:500]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    dup_instances = sum(c - 1 for c in counts.values() if c > 1)
    md = [
        "# E2E 코드 축소 리포트 (정확성 우선)", "",
        f"- 고유 내용: **{len(rows):,}개 / {total_unique_bytes/1024/1024:.2f} MB**",
        f"- 경로 인스턴스 중복: {dup_instances:,}개 (A단계, 이미 1개로 접힘)", "",
        "## B. 정규화 동등 (BOM·줄바꿈·줄끝공백만 다른 같은 코드)", "",
        f"- 그룹 **{len(norm_dupe_groups):,}개**, 추가 절감 가능 **{norm_saved/1024/1024:.2f} MB**",
        "- 원본 바이트는 파일함에 보존되므로 손실 없음.", "",
        "## C. 근접 복제 (Jaccard ≥ 0.90)", "",
        f"- 쌍 **{len(sig_pairs):,}개** (검사 대상 {len(sigs):,}개 파일)",
        "- **합치지 않음** — 사람이 판단하도록 근거만 제시(정확성 규칙).", "",
        "## 상세", "`e2e-clusters.json` 참조.",
    ]
    (DIET / "E2E-DEDUPE-REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps({**clusters, "unique_files": len(rows),
                      "unique_bytes": total_unique_bytes}, ensure_ascii=False, indent=1))
    print(f"리포트: {DIET/'E2E-DEDUPE-REPORT.md'}  클러스터: {DIET/'e2e-clusters.json'}")
    print(f"소요 {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
