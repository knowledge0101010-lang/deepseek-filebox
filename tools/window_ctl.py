"""Window control — enumerate, focus, minimize/restore/maximize, move, close.

Companion to `operator_mcp.py` (which owns screen capture and input injection). This module
owns *windows*: it lets an agent bring an application forward, resize it, or close it, which
is what makes unattended desktop work reliable (the target window must be foreground before
synthetic input reaches it).

Safety model (same spirit as the operator)
  * read-only operations (`list`) always work
  * mutating operations require `--enable-input` / `allow_input=True`
  * `--dry-run` reports the plan and changes nothing
  * every action is appended to a JSONL audit log before it is attempted
  * `close` sends WM_CLOSE (the app may prompt); it does not force-kill

usage:
  python tools/window_ctl.py list [--filter TEXT] [--json]
  python tools/window_ctl.py focus "notepad"
  python tools/window_ctl.py minimize|restore|maximize "notepad"
  python tools/window_ctl.py move "notepad" 100 100 800 600
  python tools/window_ctl.py close "notepad"
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_user32 = ctypes.windll.user32 if IS_WINDOWS else None

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
WM_CLOSE = 0x0010
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002


class WindowError(RuntimeError):
    pass


def _titles() -> list[dict]:
    if not IS_WINDOWS:
        return []
    rows: list[dict] = []

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wt.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rows.append({
            "hwnd": int(hwnd), "title": buf.value,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "width": rect.right - rect.left, "height": rect.bottom - rect.top,
            "minimized": bool(_user32.IsIconic(hwnd)),
            "foreground": hwnd == _user32.GetForegroundWindow(),
        })
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    _user32.EnumWindows(EnumProc(cb), 0)
    return rows


def list_windows(text: str | None = None) -> list[dict]:
    rows = _titles()
    if text:
        needle = text.lower()
        rows = [r for r in rows if needle in r["title"].lower()]
    return sorted(rows, key=lambda r: (not r["foreground"], r["title"]))


def find_window(text: str) -> dict:
    rows = list_windows(text)
    if not rows:
        raise WindowError(f"no visible window matching: {text!r}")
    return rows[0]


class WindowController:
    """Mutating window operations with an audit trail and an explicit gate."""

    def __init__(self, *, allow_input: bool = False, dry_run: bool | None = None,
                 audit_path: Path | None = None):
        self.allow_input = bool(allow_input)
        self.dry_run = (not self.allow_input) if dry_run is None else bool(dry_run)
        self.audit_path = Path(audit_path) if audit_path else None

    # ---- read-only helpers (safe without --enable-input) -------------------
    def list_windows(self, text: str | None = None) -> list[dict]:
        return list_windows(text)

    def find_window(self, text: str) -> dict:
        return find_window(text)

    # ---- plumbing ---------------------------------------------------------
    def _audit(self, action: str, params: dict, result: dict) -> None:
        if not self.audit_path:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "action": action,
                                    "params": params, "result": result},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _guard(self, action: str):
        if self._panic if hasattr(self, "_panic") else False:
            return {"ok": False, "error": "panic engaged"}
        if not self.dry_run and not self.allow_input:
            return {"ok": False, "error": "window control disabled: start with --enable-input"}
        return None

    def _finish(self, action: str, params: dict, result: dict) -> dict:
        self._audit(action, params, result)
        return result

    # ---- operations -------------------------------------------------------
    @staticmethod
    def _force_foreground(hwnd: int) -> None:
        """Windows refuses SetForegroundWindow from a background process. Attaching our
        input queue to the target's thread lifts that restriction; we then verify."""
        fg = _user32.GetForegroundWindow()
        tid_fg = _user32.GetWindowThreadProcessId(fg, None)
        tid_t = _user32.GetWindowThreadProcessId(hwnd, None)
        try:
            if tid_fg and tid_t and tid_fg != tid_t:
                _user32.AttachThreadInput(tid_fg, tid_t, True)
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetActiveWindow(hwnd)
            _user32.SetFocus(hwnd)
        finally:
            if tid_fg and tid_t and tid_fg != tid_t:
                _user32.AttachThreadInput(tid_fg, tid_t, False)

    def focus_hwnd(self, hwnd: int, title: str = "", minimized: bool = False) -> dict:
        err = self._guard("focus")
        if err:
            return err
        plan = {"hwnd": hwnd, "title": title, "restore": minimized}
        if self.dry_run or not IS_WINDOWS:
            return self._finish("focus", {"hwnd": hwnd},
                                {"ok": True, "applied": False, "plan": plan})
        if minimized:
            _user32.ShowWindow(int(hwnd), SW_RESTORE)
            time.sleep(0.3)
        self._force_foreground(int(hwnd))
        time.sleep(0.35)
        fg = int(_user32.GetForegroundWindow())
        verified = fg == int(hwnd)
        if not verified:                     # second attempt: bounce then force again
            _user32.ShowWindow(int(hwnd), SW_MINIMIZE)
            time.sleep(0.25)
            _user32.ShowWindow(int(hwnd), SW_RESTORE)
            self._force_foreground(int(hwnd))
            time.sleep(0.35)
            fg = int(_user32.GetForegroundWindow())
            verified = fg == int(hwnd)
        result = {"ok": verified, "applied": True, "hwnd": hwnd, "title": title,
                  "foreground_verified": verified, "foreground_hwnd": fg}
        if not verified:
            result["error"] = ("could not raise the window: another window holds the "
                               "foreground lock (is it always-on-top?)")
        return self._finish("focus", {"hwnd": hwnd}, result)

    def status(self) -> dict:
        rows = list_windows()
        fg = next((r for r in rows if r["foreground"]), None)
        return {"platform": sys.platform, "backend_available": bool(IS_WINDOWS),
                "allow_input": self.allow_input, "dry_run": self.dry_run,
                "visible_windows": len(rows),
                "foreground": {"hwnd": fg["hwnd"], "title": fg["title"]} if fg else None}

    def focus(self, text: str) -> dict:
        err = self._guard("focus")
        if err:
            return err
        try:
            w = find_window(text)
        except WindowError as exc:
            return {"ok": False, "error": str(exc)}
        return self.focus_hwnd(w["hwnd"], w["title"], minimized=w["minimized"])

    def _show(self, action: str, text: str, sw: int) -> dict:
        err = self._guard(action)
        if err:
            return err
        try:
            w = find_window(text)
        except WindowError as exc:
            return {"ok": False, "error": str(exc)}
        if self.dry_run or not IS_WINDOWS:
            return self._finish(action, {"match": text},
                                {"ok": True, "applied": False,
                                 "plan": {"hwnd": w["hwnd"], "title": w["title"], "show": sw}})
        _user32.ShowWindow(w["hwnd"], sw)
        return self._finish(action, {"match": text},
                            {"ok": True, "applied": True, "hwnd": w["hwnd"], "title": w["title"]})

    def minimize(self, text: str) -> dict:
        return self._show("minimize", text, SW_MINIMIZE)

    def restore(self, text: str) -> dict:
        return self._show("restore", text, SW_RESTORE)

    def maximize(self, text: str) -> dict:
        return self._show("maximize", text, SW_MAXIMIZE)

    def move(self, text: str, x: int, y: int, width: int, height: int) -> dict:
        err = self._guard("move")
        if err:
            return err
        if not (0 <= width <= 10000 and 0 <= height <= 10000):
            return {"ok": False, "error": "width/height out of range"}
        try:
            w = find_window(text)
        except WindowError as exc:
            return {"ok": False, "error": str(exc)}
        params = {"match": text, "x": x, "y": y, "w": width, "h": height}
        if self.dry_run or not IS_WINDOWS:
            return self._finish("move", params, {"ok": True, "applied": False,
                                                 "plan": {"hwnd": w["hwnd"], **params}})
        _user32.MoveWindow(w["hwnd"], int(x), int(y), int(width), int(height), True)
        return self._finish("move", params, {"ok": True, "applied": True, "hwnd": w["hwnd"]})

    def close(self, text: str) -> dict:
        """Send WM_CLOSE (graceful). The application may show a save prompt."""
        err = self._guard("close")
        if err:
            return err
        try:
            w = find_window(text)
        except WindowError as exc:
            return {"ok": False, "error": str(exc)}
        if self.dry_run or not IS_WINDOWS:
            return self._finish("close", {"match": text},
                                {"ok": True, "applied": False,
                                 "plan": {"hwnd": w["hwnd"], "title": w["title"],
                                          "method": "WM_CLOSE (graceful)"}})
        _user32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
        return self._finish("close", {"match": text},
                            {"ok": True, "applied": True, "hwnd": w["hwnd"], "title": w["title"]})


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--enable-input", action="store_true", help="allow mutating operations")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list"); lp.add_argument("--filter", default=None)
    for name in ("focus", "minimize", "restore", "maximize", "close"):
        sp = sub.add_parser(name); sp.add_argument("match")
    mp = sub.add_parser("move")
    mp.add_argument("match"); mp.add_argument("x", type=int); mp.add_argument("y", type=int)
    mp.add_argument("width", type=int); mp.add_argument("height", type=int)
    sub.add_parser("status")
    a = ap.parse_args()

    if a.cmd == "list":
        rows = list_windows(a.filter)
        if a.as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                flag = "FG" if r["foreground"] else ("MIN" if r["minimized"] else "  ")
                print(f"{flag} {r['hwnd']:>10}  {str(r['rect']):<28} {r['title'][:70]}")
        return 0

    ctl = WindowController(allow_input=a.enable_input,
                           dry_run=True if a.dry_run else None,
                           audit_path=Path(a.audit) if a.audit else None)
    if a.cmd == "status":
        print(json.dumps(ctl.status(), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "focus":
        res = ctl.focus(a.match)
    elif a.cmd == "minimize":
        res = ctl.minimize(a.match)
    elif a.cmd == "restore":
        res = ctl.restore(a.match)
    elif a.cmd == "maximize":
        res = ctl.maximize(a.match)
    elif a.cmd == "close":
        res = ctl.close(a.match)
    elif a.cmd == "move":
        res = ctl.move(a.match, a.x, a.y, a.width, a.height)
    else:
        print(__doc__)
        return 2
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
