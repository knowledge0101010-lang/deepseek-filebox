# Contributing

Thanks for considering a contribution. This project has an unusually strict rule, inherited
from its purpose.

## The rule

> **Compression is only allowed when losslessness is proven.**

* Level A (byte-identical, equal SHA-256) may be merged.
* Level B (equal only after normalizing BOM / line endings / trailing blanks) is reported,
  never merged.
* Level C (near-duplicate, Jaccard ≥ 0.90) is reported, never merged.

Any PR that merges B or C, or that weakens `tools/filebox_ops.py verify`, will be rejected.

## Getting started

```powershell
python -m pip install pytest        # only test dependency
python -m pytest tests -q
```

The smoke tests run entirely on a temporary fixture and need no network.

## How to propose a change

1. Describe the problem in one sentence and the measurement that would show it is fixed.
2. Add or update a test that fails before your change.
3. Keep the standard library only. No third-party runtime dependencies — the whole point is
   that this runs anywhere Python runs.
4. Update `docs/RESULTS.md` with real numbers if behaviour changes; if you did not measure
   it, do not write it.

## Reporting issues

Include: OS, Python version, the exact command, and the relevant lines from the log. If the
issue is a wrong number, include the command that reproduces the number.
