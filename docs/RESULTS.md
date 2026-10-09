# Results (measured)

Every number below was produced on this machine by the tools in this repository.
The commands are given so a third party can reproduce them.

## Dataset

| Item | Value |
|---|---|
| Sources | `C:\Users\knowl\Downloads`, `~\.codex\...\.antigravity-ide`, `문서\Antigravity`, `deepseek-harness\Antigravity`, `default-workspace\Antigravity` |
| Indexed entries | **209,902** |
| Total bytes represented | **83.04 GB** (89,150,000,000 bytes approx.) |
| Index build time | **67.4 s** (names + sizes only, no file reads) |

## Reduction ladder

| Level | Rule | Measured |
|---|---|---|
| **A** byte-identical | SHA-256 equal → one stored object | **97,356 duplicate path instances** collapsed; duplicate rate **93.2 %** |
| **B** normalized-equivalent | equal after BOM / CRLF / trailing-blank normalization | **5 groups**, 4,382 bytes of extra redundancy identified (reported, not merged) |
| **C** near-duplicate | 5-gram MinHash + LSH, Jaccard ≥ 0.90 | **75 pairs** among 1,882 candidate files (reported, never merged) |

## Storage

| Item | Value |
|---|---|
| Binaries stored as fingerprints only | **75.00 GB** |
| Unique source contents (mid-run) | **7,252 files / 5.25 MB** |
| Single consolidated file | **~133 MB** SQLite |
| Canonical DIET tree (mid-run) | 5,173 objects / 1.54 MB, 71,500 paths mapped |

## Accuracy (the point of the exercise)

Two different checks, both reported — see README §"Verification (two kinds)".

```
python tools/filebox_ops.py verify     # re-read ORIGINALS
python tools/box_selfcheck.py          # re-hash STORED CONTENT
```

| Check | Checked | Exact | Mismatch | Ratio |
|---|---|---|---|---|
| Source re-read (originals still present) | 23,168 | 23,168 | **0** | **1.0** |
| Box self-consistency (stored objects) | 12,047 | 12,047 | **0** | **1.0** |

**Honest caveat, measured:** of the 155,006 content-bearing entries, **131,838** no longer
exist at their indexed paths — the trees under `Downloads` and `Antigravity` were reorganised
after indexing. Their content is still in the box, but they cannot be verified against a
source file. An earlier partial run reported `29,500 / 29,500 exact`; that number was true for
the subset it covered and is superseded by the full-run figures above.

## Interesting finding

A single file was present **901 times** in the tree; many others **×53** and **×52**,
mostly from nested copy chains such as
`...\deepseek\deepseek\.deepseek\deepseek\Code-main\...`.
The "code you actually have" is a fraction of a percent of the bytes on disk.

## Reproduce

```powershell
python tools/filebox_build.py --phase index
python tools/filebox_build.py --phase fill
python tools/filebox_ops.py verify
python tools/dedupe_e2e.py
python tools/diet_build.py
python tools/auto_finalize.py     # the whole pipeline in one command
```
