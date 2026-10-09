r"""DeepSeek Chat bridge — drive https://chat.deepseek.com from the agent.

How it works
  * **Send**: focus the browser window, click the composer, type with SendInput (works in
    Chromium/Electron), press Enter.
  * **Read**: select-all + copy inside the page, then read the Windows clipboard. This avoids
    OCR entirely and returns *exact* text.
  * **Windows**: any window can be focused/minimized/moved through `window_ctl.py`, so the
    bridge can run without a human touching the machine.

Safety
  * input is disabled unless `--enable-input` (same gate as the operator)
  * `--dry-run` reports the plan and types nothing
  * every action is written to a JSONL audit log before it happens

usage:
  python tools/deepseek_chat.py status
  python tools/deepseek_chat.py open
  python tools/deepseek_chat.py send "안녕하세요, 파일함 통합 결과를 요약해줘"
  python tools/deepseek_chat.py read
  python tools/deepseek_chat.py ask "질문" --wait 8
  python tools/deepseek_chat.py new
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAT_URL = "https://chat.deepseek.com/"
DEFAULT_MATCH = "DeepSeek"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _clipboard_read() -> str:
    """Read the Windows clipboard as text (no third-party dependency)."""
    if sys.platform != "win32":
        return ""
    p = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "").strip()


def _clipboard_write(text: str) -> None:
    if sys.platform != "win32":
        return
    subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                   input=text, capture_output=True, text=True, encoding="utf-8")


class DeepSeekChat:
    def __init__(self, *, window_match: str = DEFAULT_MATCH, input_xy: tuple[int, int] | None = None,
                 enable_input: bool = False, dry_run: bool | None = None,
                 audit_path: Path | None = None):
        op = _load("operator_mcp", "operator_mcp.py")
        wc = _load("window_ctl", "window_ctl.py")
        self.op = op.Operator(enable_input=enable_input, dry_run=dry_run, audit_path=audit_path)
        self.ctl = wc.WindowController(allow_input=enable_input, dry_run=dry_run,
                                       audit_path=audit_path)
        self.window_match = window_match
        self.input_xy = input_xy

    # ---- window selection -------------------------------------------------
    _BROWSERS = ("chrome", "edge", "firefox", "brave", "whale", "opera")
    _EXCLUDE = ("harness", "파일 탐색기", "explorer")

    def find_chat_window(self) -> dict:
        """Pick the browser window that hosts the chat — never the DSH harness window."""
        rows = self.ctl.list_windows(self.window_match)
        rows = [r for r in rows
                if not any(x in r["title"].lower() for x in self._EXCLUDE)]
        if not rows:
            raise RuntimeError(f"no chat window for {self.window_match!r}")
        browser = [r for r in rows if any(b in r["title"].lower() for b in self._BROWSERS)]
        return (browser or rows)[0]

    # ---- helpers ---------------------------------------------------------
    def status(self) -> dict:
        err = None
        try:
            w = self.find_chat_window()
        except Exception as exc:  # noqa: BLE001 - report why, do not hide it
            w, err = None, f"{type(exc).__name__}: {exc}"
        return {
            "platform": sys.platform,
            "url": CHAT_URL,
            "window_match": self.window_match,
            "window": {"hwnd": w["hwnd"], "title": w["title"], "rect": w["rect"]} if w else None,
            "window_lookup_error": err,
            "candidates": [r["title"] for r in self.ctl.list_windows(self.window_match)[:5]],
            "input_enabled": self.op.enable_input and not self.op._panic,
            "dry_run": self.op.dry_run,
            "input_xy": list(self.input_xy) if self.input_xy else None,
        }

    def _composer_point(self) -> tuple[int, int]:
        """Where the message box is.

        chat.deepseek.com puts the composer in the lower middle of the content column
        (sidebar on the left, roughly 63% down the viewport). `--input-xy X,Y` overrides.
        """
        if self.input_xy:
            return self.input_xy
        w = self.find_chat_window()
        left, top, right, bottom = w["rect"]
        cx = left + int((right - left) * 0.69)
        cy = top + int((bottom - top) * 0.655)
        return (cx, cy)

    def _focus(self) -> dict:
        try:
            w = self.find_chat_window()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return self.ctl.focus_hwnd(w["hwnd"], w["title"], minimized=w["minimized"])

    # ---- actions ---------------------------------------------------------
    def open(self) -> dict:
        if self.op.dry_run or sys.platform != "win32":
            return {"ok": True, "applied": False, "plan": {"launch": ["msedge.exe", CHAT_URL]}}
        subprocess.Popen(["cmd", "/c", "start", "", "msedge.exe", CHAT_URL])
        time.sleep(4.0)
        return {"ok": True, "applied": True, "url": CHAT_URL,
                "window": self.status().get("window")}

    def send(self, text: str) -> dict:
        if not text.strip():
            return {"ok": False, "error": "empty message"}
        f = self._focus()
        if not f.get("ok"):
            return f
        x, y = self._composer_point()
        click = self.op.mouse_click(x, y, "left", 1)
        if not click.get("ok"):
            return click
        time.sleep(0.4)
        typed = self.op.key_type(text)
        if not typed.get("ok"):
            return typed
        time.sleep(0.4)
        enter = self.op.key_press(["ENTER"])
        if not enter.get("ok"):
            return enter
        return {"ok": True, "applied": typed.get("applied"), "chars": len(text),
                "at": [x, y]}

    def read(self) -> dict:
        f = self._focus()
        if not f.get("ok"):
            return f
        self.op.key_press(["CTRL", "A"])
        time.sleep(0.3)
        self.op.key_press(["CTRL", "C"])
        time.sleep(0.8)
        text = _clipboard_read()
        return {"ok": bool(text), "chars": len(text), "text": text}

    def ask(self, text: str, wait: float = 8.0) -> dict:
        sent = self.send(text)
        if not sent.get("ok"):
            return sent
        if self.op.dry_run:
            return {"ok": True, "applied": False, "sent": sent, "note": "dry-run: nothing read"}
        time.sleep(max(1.0, wait))
        got = self.read()
        return {"ok": got.get("ok", False), "sent": sent, "reply": got.get("text", ""),
                "reply_chars": got.get("chars", 0)}

    def new_chat(self) -> dict:
        f = self._focus()
        if not f.get("ok"):
            return f
        # DeepSeek web: the sidebar has "새 채팅"/"New chat"; Ctrl+Shift+O is not guaranteed,
        # so fall back to clicking the top-left new-chat affordance.
        w = self.ctl.find_window(self.window_match)
        left, top, _, _ = w["rect"]
        x, y = left + 150, top + 120
        return self.op.mouse_click(x, y, "left", 1) | {"at": [x, y]}


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--enable-input", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", default=None)
    ap.add_argument("--match", default=DEFAULT_MATCH, help="window title substring")
    ap.add_argument("--input-xy", default=None, help="composer coordinates X,Y")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("open"); sub.add_parser("read"); sub.add_parser("new")
    sp = sub.add_parser("send"); sp.add_argument("text")
    ap_ = sub.add_parser("ask"); ap_.add_argument("text"); ap_.add_argument("--wait", type=float, default=8.0)
    a = ap.parse_args()

    xy = None
    if a.input_xy:
        try:
            xs, ys = a.input_xy.split(",")
            xy = (int(xs), int(ys))
        except ValueError:
            print("--input-xy must be X,Y", file=sys.stderr)
            return 2

    chat = DeepSeekChat(window_match=a.match, input_xy=xy,
                        enable_input=a.enable_input,
                        dry_run=True if a.dry_run else None,
                        audit_path=Path(a.audit) if a.audit else None)

    if a.cmd == "status":
        print(json.dumps(chat.status(), ensure_ascii=False, indent=1))
    elif a.cmd == "open":
        print(json.dumps(chat.open(), ensure_ascii=False))
    elif a.cmd == "send":
        print(json.dumps(chat.send(a.text), ensure_ascii=False))
    elif a.cmd == "read":
        print(json.dumps(chat.read(), ensure_ascii=False))
    elif a.cmd == "ask":
        print(json.dumps(chat.ask(a.text, a.wait), ensure_ascii=False))
    elif a.cmd == "new":
        print(json.dumps(chat.new_chat(), ensure_ascii=False))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
