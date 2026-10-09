# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · SemVer.

## [0.1.0] — 2026-10-09

### Added
- `filebox_build.py` — two-phase builder: `index` (names + sizes only) and `fill`
  (content hashing, resumable, commits every 500 files). Supports `--sources` and `--db`.
- `filebox_ops.py` — `stats`, `dedupe`, `verify` (end-to-end SHA-256 re-read),
  `canonical` (unique objects + `MAP.json`).
- `filebox_query.py` — read-only CLI (`stats`, `list`, `blobs`, `cat`, `extract`).
- `dedupe_e2e.py` — three-level reduction (A byte-identical, B normalized-equivalent,
  C near-duplicate MinHash/LSH) with explicit accuracy rules.
- `diet_build.py` — canonical deduplicated tree + hard-linked filebox in one place.
- `auto_finalize.py` — unattended pipeline: fill → verify → e2e → diet → report.
- Smoke tests that run on a temporary fixture; CI matrix for Linux/Windows × Python 3.10/3.12.

### Measured on first run
- 209,902 entries indexed over 83.04 GB in 67.4 s.
- 97,356 duplicate path instances collapsed (93.2 % duplicate rate).
- 75.00 GB of binaries stored as fingerprints only.
- Verification: 29,500 / 29,500 exact, 0 mismatch (ratio 1.0).

### Fixed during bring-up
- `canonical` reused a single SQLite cursor, truncating the outer loop and writing one
  object. Cursor is no longer nested; mappings are pre-loaded.
- Duplicate-savings metric compared unique bytes to themselves, reporting `0`. It now
  compares total instance bytes against stored unique bytes.
- `diet_build.py` under-counted `unique_bytes` by counting only newly written objects.
