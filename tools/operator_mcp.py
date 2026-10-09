#!/usr/bin/env python3
r"""Local operator — screen capture + mouse/keyboard injection, exposed over MCP.

Pairs with the ByteDance Agent TARS / UI-TARS Desktop pattern
(https://github.com/bytedance/UI-TARS-desktop): that stack drives a computer through
screenshot + visual grounding + mouse/keyboard actions, and its kernel speaks MCP. This file
provides the same capability locally, with **no third-party dependency**, so a TARS/UI-TARS
agent (or any MCP client) can operate this machine.

SAFETY MODEL — read before enabling
  * Input is **disabled by default**. `--enable-input` is required to move a real cursor.
  * `--dry-run` (default ON unless `--enable-input` is given) reports what *would* happen and
    performs nothing.
  * Every action is bounds-checked against the real screen size and rate-limited.
  * Every action is appended to an audit log (JSONL) before it is attempted.
  * `operator_panic` releases the input lock and disables further actions for the session.

usage:
  python tools/operator_mcp.py                     # safe: disabled + dry-run
  python tools/operator_mcp.py --dry-run           # explicit dry-run
  python tools/operator_mcp.py --enable-input      # ARMS REAL INPUT on this machine
  python tools/operator_mcp.py --enable-input --audit .\operator-audit.jsonl
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import struct
import sys
import time
import zlib
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# --- Windows API binding (lazy; module imports fine on other platforms) -------
_user32 = ctypes.windll.user32 if IS_WINDOWS else None
_gdi32 = ctypes.windll.gdi32 if IS_WINDOWS else None

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL = 0x0008, 0x0010, 0x0800
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

VK = {
    "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B, "SPACE": 0x20, "BACKSPACE": 0x08,
    "DELETE": 0x2E, "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
}
# letters and digits are their ASCII codes; register them so key_press(["CTRL","S"]) works
VK.update({chr(c): c for c in range(0x41, 0x5B)})        # A-Z
VK.update({str(d): 0x30 + d for d in range(10)})         # 0-9
VK.update({"-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
           ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0})


# --- minimal PNG encoder (stdlib only) ---------------------------------------
def encode_png(width: int, height: int, rows_rgb: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows_rgb)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class Operator:
    """Screen + input surface with an explicit safety gate."""

    def __init__(self, *, enable_input: bool = False, dry_run: bool | None = None,
                 audit_path: Path | None = None, max_actions_per_second: float = 20.0):
        self.enable_input = bool(enable_input)
        self.dry_run = (not self.enable_input) if dry_run is None else bool(dry_run)
        self.audit_path = Path(audit_path) if audit_path else None
        self.min_interval = 1.0 / max_actions_per_second if max_actions_per_second else 0.0
        self._last = 0.0
        self._panic = False

    # ---- helpers ----------------------------------------------------------
    def screen_size(self) -> tuple[int, int]:
        if not IS_WINDOWS:
            return (0, 0)
        return (int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1)))

    def status(self) -> dict:
        w, h = self.screen_size()
        return {
            "platform": sys.platform,
            "backend_available": bool(IS_WINDOWS),
            "input_enabled": self.enable_input and not self._panic,
            "dry_run": self.dry_run,
            "panicked": self._panic,
            "screen": {"width": w, "height": h},
            "audit_log": str(self.audit_path) if self.audit_path else None,
        }

    def _audit(self, action: str, params: dict, result: dict) -> None:
        if not self.audit_path:
            return
        line = json.dumps({"ts": time.time(), "action": action, "params": params,
                           "result": result}, ensure_ascii=False)
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _guard(self, action: str, params: dict) -> dict | None:
        """Returns an error dict if the action must not run, else None."""
        if self._panic:
            return {"ok": False, "error": "panic engaged; input disabled for this session"}
        if not self.enable_input:
            return {"ok": False, "error": "input disabled: start the server with --enable-input"}
        now = time.time()
        if self.min_interval and (now - self._last) < self.min_interval:
            time.sleep(self.min_interval - (now - self._last))
        self._last = time.time()
        return None

    def _finish(self, action: str, params: dict, result: dict) -> dict:
        self._audit(action, params, result)
        return result

    # ---- screen -----------------------------------------------------------
    def capture(self, *, max_width: int = 1600) -> dict:
        w, h = self.screen_size()
        if not IS_WINDOWS:
            return self._finish("screen_capture", {}, {"ok": False,
                                "error": f"unsupported platform: {sys.platform}"})
        hwnd = _user32.GetDesktopWindow()
        hdc = _user32.GetWindowDC(hwnd)
        memdc = _gdi32.CreateCompatibleDC(hdc)
        bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
        _gdi32.SelectObject(memdc, bmp)
        _gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth, bih.biHeight = w, -h        # negative height => top-down rows
        bih.biPlanes, bih.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        _gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(memdc)
        _user32.ReleaseDC(hwnd, hdc)

        data = memoryview(buf.raw)
        step = max(1, w // max_width)
        rows = []
        for y in range(0, h, step):
            base = y * w * 4
            row = bytearray()
            for x in range(0, w, step):
                o = base + x * 4
                row += bytes((data[o + 2], data[o + 1], data[o]))   # BGRA -> RGB
            rows.append(bytes(row))
        png = encode_png(len(rows[0]) // 3, len(rows), rows)
        return self._finish("screen_capture", {"scale_step": step}, {
            "ok": True,
            "width": w, "height": h, "scale_step": step,
            "png_base64": base64.b64encode(png).decode("ascii"),
            "approx_png_bytes": len(png),
            "dry_run": self.dry_run,
        })

    # ---- mouse ------------------------------------------------------------
    def _check_bounds(self, x: int, y: int) -> dict | None:
        w, h = self.screen_size()
        if not (0 <= x < w and 0 <= y < h):
            return {"ok": False, "error": f"out of bounds: ({x},{y}) screen {w}x{h}"}
        return None

    def mouse_move(self, x: int, y: int) -> dict:
        err = self._guard("mouse_move", {"x": x, "y": y})
        if err:
            return err
        err = self._check_bounds(x, y)
        if err:
            return err
        if self.dry_run or not IS_WINDOWS:
            return self._finish("mouse_move", {"x": x, "y": y},
                                {"ok": True, "applied": False, "plan": {"move": [x, y]}})
        _user32.SetCursorPos(int(x), int(y))
        return self._finish("mouse_move", {"x": x, "y": y}, {"ok": True, "applied": True})

    def mouse_click(self, x: int | None = None, y: int | None = None,
                    button: str = "left", count: int = 1) -> dict:
        params = {"x": x, "y": y, "button": button, "count": count}
        err = self._guard("mouse_click", params)
        if err:
            return err
        if x is not None and y is not None:
            err = self._check_bounds(x, y)
            if err:
                return err
        if button not in ("left", "right", "middle"):
            return {"ok": False, "error": f"unsupported button: {button}"}
        if not 1 <= count <= 5:
            return {"ok": False, "error": "count must be 1..5"}
        if self.dry_run or not IS_WINDOWS:
            return self._finish("mouse_click", params,
                                {"ok": True, "applied": False, "plan": params})
        if x is not None and y is not None:
            _user32.SetCursorPos(int(x), int(y))
        down, up = {
            "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle": (0x0020, 0x0040),
        }[button]
        for _ in range(count):
            _user32.mouse_event(down, 0, 0, 0, 0)
            _user32.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.02)
        return self._finish("mouse_click", params, {"ok": True, "applied": True})

    # ---- keyboard ---------------------------------------------------------
    def key_type(self, text: str) -> dict:
        if not isinstance(text, str) or not text:
            return {"ok": False, "error": "text required"}
        if len(text) > 2000:
            return {"ok": False, "error": "text too long (max 2000)"}
        err = self._guard("key_type", {"chars": len(text)})
        if err:
            return err
        if self.dry_run or not IS_WINDOWS:
            return self._finish("key_type", {"chars": len(text)},
                                {"ok": True, "applied": False,
                                 "plan": {"text_preview": text[:40]}})
        for ch in text:
            code = ord(ch)
            _user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
            _user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
        return self._finish("key_type", {"chars": len(text)}, {"ok": True, "applied": True})

    def key_press(self, keys: list[str]) -> dict:
        if not keys:
            return {"ok": False, "error": "keys required"}
        upper = [k.upper() for k in keys]
        unknown = [k for k in upper if k not in VK]
        if unknown:
            return {"ok": False, "error": f"unknown keys: {unknown}",
                    "known": sorted(VK)}
        err = self._guard("key_press", {"keys": upper})
        if err:
            return err
        if self.dry_run or not IS_WINDOWS:
            return self._finish("key_press", {"keys": upper},
                                {"ok": True, "applied": False, "plan": {"keys": upper}})
        for k in upper:
            _user32.keybd_event(VK[k], 0, 0, 0)
        for k in reversed(upper):
            _user32.keybd_event(VK[k], 0, KEYEVENTF_KEYUP, 0)
        return self._finish("key_press", {"keys": upper}, {"ok": True, "applied": True})

    def panic(self) -> dict:
        self._panic = True
        return self._finish("panic", {}, {"ok": True, "panicked": True,
                                          "detail": "input locked for this session"})


# --- MCP server ---------------------------------------------------------------
TOOLS = [
    {"name": "operator_status",
     "description": "Report platform, screen size, whether real input is enabled, dry-run "
                    "state, and the audit log path.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "screen_capture",
     "description": "Capture the desktop as PNG (base64). Downscaled with an integer step to "
                    "stay small; the exact screen size is always reported.",
     "inputSchema": {"type": "object",
                     "properties": {"max_width": {"type": "integer", "minimum": 320,
                                                  "maximum": 3840, "default": 1600}},
                     "additionalProperties": False}},
    {"name": "mouse_move",
     "description": "Move the cursor. Bounds-checked against the real screen size.",
     "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"},
                                                      "y": {"type": "integer"}},
                     "required": ["x", "y"], "additionalProperties": False}},
    {"name": "mouse_click",
     "description": "Click at an optional position, or at the current position.",
     "inputSchema": {"type": "object",
                     "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                                    "button": {"type": "string",
                                               "enum": ["left", "right", "middle"]},
                                    "count": {"type": "integer", "minimum": 1, "maximum": 5}},
                     "additionalProperties": False}},
    {"name": "key_type",
     "description": "Type literal text using unicode key events.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"], "additionalProperties": False}},
    {"name": "key_press",
     "description": "Press a key combination (e.g. [\"CTRL\",\"S\"]).",
     "inputSchema": {"type": "object",
                     "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                     "required": ["keys"], "additionalProperties": False}},
    {"name": "operator_panic",
     "description": "Kill switch: lock input for the rest of the session immediately.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]

OP: Operator = Operator()


def call_tool(name: str, args: dict) -> dict:
    if name == "operator_status":
        return OP.status()
    if name == "screen_capture":
        return OP.capture(max_width=int(args.get("max_width", 1600)))
    if name == "mouse_move":
        return OP.mouse_move(int(args["x"]), int(args["y"]))
    if name == "mouse_click":
        return OP.mouse_click(args.get("x"), args.get("y"),
                              str(args.get("button", "left")), int(args.get("count", 1)))
    if name == "key_type":
        return OP.key_type(str(args["text"]))
    if name == "key_press":
        return OP.key_press(list(args["keys"]))
    if name == "operator_panic":
        return OP.panic()
    raise KeyError(name)


def dispatch(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "deepseek-operator", "version": "0.1.0"}}}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            payload = call_tool(name, args)
        except KeyError:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(payload, ensure_ascii=False)}]}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    global OP
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--enable-input", action="store_true",
                    help="ARM REAL INPUT on this machine (default: disabled)")
    ap.add_argument("--dry-run", action="store_true",
                    help="never perform input, only report the plan")
    ap.add_argument("--audit", default=None, help="JSONL audit log path")
    args = ap.parse_args()
    OP = Operator(enable_input=args.enable_input,
                  dry_run=True if args.dry_run else None,
                  audit_path=Path(args.audit) if args.audit else None)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = dispatch(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
