"""Cross-platform startup splash shown before heavy imports finish.

Opened by ``_kreuzberg_launcher`` (or optionally early in ``main``) and closed
once the Flet UI is ready (``ui.app.run_app``).
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

APP_NAME = "KreuzbergParser"
_SPLASH_MESSAGE = "Iniciando…"

# Process / thread handle for the active splash (one at a time).
_proc: Optional[subprocess.Popen] = None
_ready_file: Optional[Path] = None
_win_thread: Optional[threading.Thread] = None
_win_hwnd = None  # ctypes HWND on Windows
_closed = False
_lock = threading.Lock()


def _resolve_logo(base_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate assets/kreuzberg-parser.png relative to package or CWD."""
    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append(base_dir / "assets" / "kreuzberg-parser.png")
        candidates.append(base_dir / "kreuzberg-parser.png")
    # PACKAGE_DIR when config is already importable (dev / after path setup).
    try:
        from config import Config

        candidates.append(Config.PACKAGE_DIR / "assets" / "kreuzberg-parser.png")
    except Exception:
        pass
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "assets" / "kreuzberg-parser.png")
    env = (os.environ.get("KREUZBERG_PARSER_SPLASH_IMAGE") or "").strip()
    if env:
        candidates.insert(0, Path(env))

    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def _linux_splash_script(base_dir: Optional[Path] = None) -> Optional[Path]:
    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append(base_dir / "packaging" / "linux" / "splash.py")
        candidates.append(base_dir / "splash.py")
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "packaging" / "linux" / "splash.py")
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def _start_linux(logo: Path, base_dir: Optional[Path]) -> bool:
    global _proc, _ready_file

    script = _linux_splash_script(base_dir)
    system_python = Path("/usr/bin/python3")
    if not system_python.is_file():
        system_python = Path(shutil_which("python3") or "")
    if not script or not system_python.is_file():
        return _start_linux_zenity_fallback()

    ready = Path(tempfile.gettempdir()) / f"kreuzberg-parser-splash-{os.getpid()}.ready"
    try:
        if ready.exists():
            ready.unlink()
    except OSError:
        pass
    _ready_file = ready

    try:
        _proc = subprocess.Popen(
            [
                str(system_python),
                str(script),
                "--image",
                str(logo),
                "--title",
                APP_NAME,
                "--message",
                _SPLASH_MESSAGE,
                "--ready-file",
                str(ready),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        _proc = None
        _ready_file = None
        return _start_linux_zenity_fallback()


def shutil_which(cmd: str) -> Optional[str]:
    import shutil

    return shutil.which(cmd)


def _start_linux_zenity_fallback() -> bool:
    global _proc
    zenity = shutil_which("zenity")
    if not zenity:
        return False
    try:
        _proc = subprocess.Popen(
            [
                zenity,
                "--progress",
                "--pulsate",
                "--no-cancel",
                "--auto-close",
                f"--title={APP_NAME}",
                f"--text={_SPLASH_MESSAGE}",
                "--width=320",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        _proc = None
        return False


def _start_windows(logo: Optional[Path]) -> bool:
    """Show a simple Win32 splash in a daemon thread (ctypes, no extra deps)."""
    global _win_thread, _win_hwnd

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    WS_BORDER = 0x00800000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    SW_SHOW = 5
    WM_DESTROY = 0x0002
    WM_PAINT = 0x000F
    WM_CLOSE = 0x0010
    DT_CENTER = 0x0001
    DT_VCENTER = 0x0004
    DT_SINGLELINE = 0x0020
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", wintypes.HDC),
            ("fErase", wintypes.BOOL),
            ("rcPaint", wintypes.RECT),
            ("fRestore", wintypes.BOOL),
            ("fIncUpdate", wintypes.BOOL),
            ("rgbReserved", ctypes.c_char * 32),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 1),
        ]

    bmp_bits: Optional[bytes] = None
    bmp_w = 0
    bmp_h = 0
    if logo is not None and logo.is_file():
        try:
            from PIL import Image

            img = Image.open(logo).convert("RGB")
            img.thumbnail((160, 160))
            bmp_w, bmp_h = img.size
            # Bottom-up BGR for SetDIBitsToDevice.
            raw = img.tobytes("raw", "BGR")
            stride = ((bmp_w * 3 + 3) // 4) * 4
            rows = []
            for y in range(bmp_h - 1, -1, -1):
                row = raw[y * bmp_w * 3 : (y + 1) * bmp_w * 3]
                rows.append(row + b"\x00" * (stride - bmp_w * 3))
            bmp_bits = b"".join(rows)
        except Exception:
            bmp_bits = None

    state = {"hwnd": None, "quit": False}

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            brush = gdi32.CreateSolidBrush(0x003D2E1A)  # dark teal BGR
            user32.FillRect(hdc, ctypes.byref(rect), brush)
            gdi32.DeleteObject(brush)

            if bmp_bits and bmp_w and bmp_h:
                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = bmp_w
                bmi.bmiHeader.biHeight = bmp_h
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 24
                bmi.bmiHeader.biCompression = BI_RGB
                x = (rect.right - bmp_w) // 2
                y = 28
                gdi32.SetDIBitsToDevice(
                    hdc,
                    x,
                    y,
                    bmp_w,
                    bmp_h,
                    0,
                    0,
                    0,
                    bmp_h,
                    bmp_bits,
                    ctypes.byref(bmi),
                    DIB_RGB_COLORS,
                )
                text_top = y + bmp_h + 16
            else:
                text_top = rect.bottom // 2 - 10

            text_rect = wintypes.RECT(0, text_top, rect.right, text_top + 28)
            gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
            gdi32.SetTextColor(hdc, 0x00FFFFFF)
            user32.DrawTextW(
                hdc,
                _SPLASH_MESSAGE,
                -1,
                ctypes.byref(text_rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE,
            )
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_CLOSE or msg == WM_DESTROY:
            state["quit"] = True
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # Keep callback alive for the lifetime of the window thread.
    wnd_proc_cb = WNDPROC(wnd_proc)
    _start_windows._wnd_proc_cb = wnd_proc_cb  # type: ignore[attr-defined]

    def run_loop() -> None:
        global _win_hwnd
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "KreuzbergParserSplash"
        wc = WNDCLASS()
        wc.lpfnWndProc = wnd_proc_cb
        wc.hInstance = hinstance
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        wc.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            # ERROR_CLASS_ALREADY_EXISTS (1410) is fine on relaunch.
            if ctypes.get_last_error() not in (0, 1410):
                return

        width, height = 320, 280
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            class_name,
            APP_NAME,
            WS_POPUP | WS_VISIBLE | WS_BORDER,
            x,
            y,
            width,
            height,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            return
        state["hwnd"] = hwnd
        _win_hwnd = hwnd
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.UpdateWindow(hwnd)

        msg = wintypes.MSG()
        while not state["quit"] and not _closed:
            has = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if has:
                if msg.message == 0x0012:  # WM_QUIT
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                kernel32.Sleep(20)

        if state["hwnd"]:
            user32.DestroyWindow(state["hwnd"])
            state["hwnd"] = None
            _win_hwnd = None

    _win_thread = threading.Thread(target=run_loop, name="startup-splash", daemon=True)
    _win_thread.start()
    return True


def show_splash(base_dir: Optional[Path] = None) -> bool:
    """Open the startup splash. Safe to call more than once (no-op if open)."""
    global _closed
    with _lock:
        if _proc is not None or (_win_thread is not None and _win_thread.is_alive()):
            return True
        _closed = False
        logo = _resolve_logo(base_dir)
        ok = False
        if sys.platform == "win32":
            ok = _start_windows(logo)
        else:
            if logo is None:
                ok = _start_linux_zenity_fallback()
            else:
                ok = _start_linux(logo, base_dir)
        if ok:
            atexit.register(close_splash)
        return ok


def close_splash() -> None:
    """Dismiss the splash (idempotent)."""
    global _proc, _ready_file, _win_hwnd, _closed, _win_thread

    with _lock:
        if _closed and _proc is None and _win_hwnd is None:
            return
        _closed = True

        if _ready_file is not None:
            try:
                _ready_file.write_text("1", encoding="utf-8")
            except OSError:
                pass

        if _proc is not None:
            proc = _proc
            _proc = None
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass

        if _ready_file is not None:
            try:
                if _ready_file.exists():
                    _ready_file.unlink()
            except OSError:
                pass
            _ready_file = None

        if sys.platform == "win32" and _win_hwnd:
            try:
                import ctypes

                ctypes.windll.user32.PostMessageW(_win_hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
            _win_hwnd = None

        thread = _win_thread
        _win_thread = None

    if thread is not None and thread.is_alive():
        thread.join(timeout=1.5)
