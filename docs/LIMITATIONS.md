# Limitations

Read this before trusting any number in this repository.

## Scope

1. **This is not a backup.** Originals are the source of truth. The filebox stores an index,
   a content store for text files up to a size cap, and fingerprints for everything else.
   If the originals are deleted, the box cannot fully restore them.
2. **Binaries are not stored.** Files above `--max-mb` (default 8 MB) are recorded as
   `size + partial SHA-256 fingerprint` only. That is a deliberate trade: it is where 75 GB
   of the measured 83 GB went. Restoration of those files requires the original.
3. **Level B and C never merge.** Normalized-equivalent and near-duplicate findings are
   *reported*; only byte-identical (level A) content is collapsed. This keeps the accuracy
   claim honest, at the cost of leaving some redundancy on the table.

## Measurement caveats

4. **The mid-run numbers move.** The fill phase is resumable and processes files in
   ascending size order, so `unique contents` and `duplicate rate` change while it runs.
   Numbers in `docs/RESULTS.md` are stamped mid-run and say so.
5. **`verify` was run on the filled subset**, not on every indexed entry. A full recount is
   produced by `auto_finalize.py` when the fill completes.
6. **Near-duplicate detection is bounded.** Files outside `[1 KiB, 1 MiB]` are skipped, and
   the candidate list is capped (`--max-files`, default 20,000). It is a screening tool, not
   an exhaustive clone census.
7. **MinHash uses a Python `hash()`-free but non-cryptographic permutation family.** It is
   calibrated by an explicit Jaccard threshold; do not treat it as a cryptographic digest.

## Environment

8. Windows-first: default paths are absolute Windows paths. The tools accept `--db` and
   `--sources`, so other layouts work, but the defaults will not.
9. SQLite is a single writer. Do not run two `fill` processes against the same database.
10. OneDrive/cloud-backed folders can be slow (hydration) or lock files while syncing; the
    `fill` phase is intentionally restartable for this reason.

## Not covered

11. **No secret scanning, no malware scanning, no code review.** This tool indexes and
    deduplicates bytes; it makes no claims about what those bytes do.
12. **No provenance guarantees.** A hash proves two files are identical, not that either is
    trustworthy or licensed for reuse. Check the licences of the content you box.
