# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · SemVer.

## [0.1.0] — 2026-10-09

### Added
- `filebox_build.py` — two-phase builder: `index` (names + sizes only) and `fill`
  (content hashing, resumable, commits every 500 files). Supports `--sources` and `--db`.
- `filebox_mcp.py` — MCP (stdio JSON-RPC) server exposing the read-only filebox tools, so a
  ByteDance **Agent TARS / UI-TARS Desktop** agent can mount it (its kernel is MCP-based).
  See `docs/INTEGRATION-UI-TARS.md`.
- `operator_mcp.py` — local operator over MCP: desktop `screen_capture` (stdlib PNG encoder),
  `mouse_move` / `mouse_click`, `key_type` / `key_press`, and an `operator_panic` kill switch.
  Input is **disabled by default** (`--enable-input` to arm), dry-run reports plans without
  acting, coordinates are bounds-checked, actions are rate-limited, and every attempt is
  appended to a JSONL audit log. See `docs/OPERATOR.md`.
- `box_selfcheck.py` — re-hashes stored objects against their hash keys (box internal
  integrity), for the case where originals are gone.
- `filebox_ops.py` — `stats`, `dedupe`, `verify` (end-to-end SHA-256 re-read),
  `canonical` (unique objects + `MAP.json`).
- `filebox_query.py` — read-only CLI (`stats`, `list`, `blobs`, `cat`, `extract`).
- `dedupe_e2e.py` — three-level reduction (A byte-identical, B normalized-equivalent,
  C near-duplicate MinHash/LSH) with explicit accuracy rules.
- `diet_build.py` — canonical deduplicated tree + hard-linked filebox in one place.
- `auto_finalize.py` — unattended pipeline: fill → verify → e2e → diet → report.
- Test suites for the CLI tools (`tests/test_smoke.py`) and the MCP surface
  (`tests/test_mcp.py`); CI matrix for Linux/Windows × Python 3.10/3.12.

### Measured on first full run
- 209,902 entries indexed over 83.04 GB in 67.4 s; fill completed (0 pending).
- 155,006 content-bearing entries collapse to **12,047 unique objects / 273.3 MB**;
  142,959 duplicate instances removed (**466.7 MB of 776.0 MB**).
- 75.00 GB of binaries stored as fingerprints only.
- Source re-read: 23,168 / 23,168 exact, 0 mismatch. Box self-consistency: 12,047 / 12,047 = 1.0.
- 131,838 originals were gone from their indexed paths by verification time (content preserved).
- Near-duplicate screening (Jaccard ≥ 0.90): 823 pairs among 6,574 files — reported, not merged.

### Fixed during bring-up
- `canonical` reused a single SQLite cursor, truncating the outer loop and writing one
  object. Cursor is no longer nested; mappings are pre-loaded.
- Duplicate-savings metric compared unique bytes to themselves, reporting `0`. It now
  compares total instance bytes against stored unique bytes.
- `diet_build.py` under-counted `unique_bytes` by counting only newly written objects.
- `filebox_build.py` / `filebox_ops.py`: `global` declared after use → SyntaxError. Caught by
  the new tests; declarations moved to the top of `main()`.
- `--db` was rejected when placed after a subcommand; both CLIs now accept it in either
  position via a shared parent parser.
- MCP `filebox_stats` was called with an argument it did not accept (now `_args=None`).
