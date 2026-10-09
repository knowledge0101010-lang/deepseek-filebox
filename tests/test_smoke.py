"""End-to-end smoke tests — run entirely on a temporary fixture, no network.

Covers: index -> fill -> dedupe by content hash -> end-to-end verification.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def run(script: str, *args: str) -> str:
    proc = subprocess.run([sys.executable, str(TOOLS / script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"{script} failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return proc.stdout


def make_fixture(root: Path) -> Path:
    src = root / "src"
    (src / "pkg").mkdir(parents=True, exist_ok=True)
    (src / "pkg" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    # byte-identical duplicate -> must collapse
    (src / "pkg" / "app_copy.py").write_text("print('hello')\n", encoding="utf-8")
    (src / "copy2" / "pkg").mkdir(parents=True, exist_ok=True)
    (src / "copy2" / "pkg" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    # normalized-equal only (CRLF) -> different hash, reported by e2e but NOT merged
    (src / "pkg" / "crlf.py").write_text("print('hello')\r\n", encoding="utf-8")
    # unique file
    (src / "pkg" / "other.py").write_text("value = 42\n", encoding="utf-8")
    # binary -> fingerprint only
    (src / "pkg" / "blob.bin").write_bytes(b"\x00\x01\x02" * 50)
    return src


def test_index_fill_verify_and_dedupe(tmp_path):
    src = make_fixture(tmp_path)
    db = tmp_path / "box.db"

    run("filebox_build.py", "--phase", "all", "--sources", f"{src}=test", "--db", str(db))

    con = sqlite3.connect(db)
    entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    kinds = dict(con.execute("SELECT kind, COUNT(*) FROM entries GROUP BY kind"))
    unique = con.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    dup_rows = con.execute(
        "SELECT COUNT(*) AS c FROM entries WHERE has_content=1 GROUP BY hash "
        "ORDER BY c DESC LIMIT 1").fetchone()
    con.close()

    assert entries == 6, f"expected 6 indexed entries, got {entries}"
    assert kinds.get("blob") == 1, kinds            # .bin stays a fingerprint
    # app.py, app_copy.py, copy2/app.py are byte-identical -> exactly one stored object,
    # other.py and crlf.py are distinct -> 3 unique contents total
    assert unique == 3, f"expected 3 unique contents, got {unique}"
    assert dup_rows and dup_rows[0] == 3, dup_rows   # one hash owns 3 paths


def test_verify_roundtrip_is_exact(tmp_path):
    src = make_fixture(tmp_path)
    db = tmp_path / "box.db"
    run("filebox_build.py", "--phase", "all", "--sources", f"{src}=test", "--db", str(db))

    out = run("filebox_ops.py", "verify", "--db", str(db))
    assert '"mismatch": 0' in out, out
    assert '"exact_ratio": 1.0' in out, out


def test_query_lists_and_cats(tmp_path):
    src = make_fixture(tmp_path)
    db = tmp_path / "box.db"
    run("filebox_build.py", "--phase", "all", "--sources", f"{src}=test", "--db", str(db))

    listing = run("filebox_query.py", "list", "--db", str(db), "--limit", "5")
    assert "app.py" in listing

    obj = src / "pkg" / "app.py"
    content = run("filebox_query.py", "cat", "--db", str(db), str(obj))
    assert "print('hello')" in content


def test_box_selfcheck_is_exact(tmp_path):
    """Stored objects must hash back to their own keys (originals not needed)."""
    src = make_fixture(tmp_path)
    db = tmp_path / "box.db"
    run("filebox_build.py", "--phase", "all", "--sources", f"{src}=test", "--db", str(db))

    out = run("box_selfcheck.py", "--db", str(db))
    assert '"mismatch": 0' in out, out
    assert '"exact_ratio": 1.0' in out, out


def test_verify_reports_missing_originals(tmp_path):
    """If an original disappears, verify must count it as unreadable, not as a mismatch."""
    src = make_fixture(tmp_path)
    db = tmp_path / "box.db"
    run("filebox_build.py", "--phase", "all", "--sources", f"{src}=test", "--db", str(db))

    (src / "pkg" / "other.py").unlink()

    out = run("filebox_ops.py", "verify", "--db", str(db))
    assert '"mismatch": 0' in out, out
    assert '"source_unreadable": 1' in out, out
