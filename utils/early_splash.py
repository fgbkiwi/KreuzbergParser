"""Early Windows splash shown before Flet / heavy imports are ready.

The installed app can take several seconds to import Torch/Kreuzberg/etc.
This module opens a lightweight Win32 window (ctypes + Pillow only) so the
user sees the animated branding immediately, with an \"Aguarde...\" caption
drawn under the GIF's own \"Kiwi Down\" text (no extra footer band).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_INSTANCE: Optional["EarlySplash"] = None
_DEBUG_LOG: Optional[Path] = None

# Match the baked-in "Kiwi Down" baseline in the source GIF (480x526).
_SRC_WIDTH = 480
_SRC_HEIGHT = 526
_SPLASH_SIDE_CROP_PX = 1
_DISPLAY_WIDTH = 440
# "Aguarde..." sits just below the animated "Kiwi Down" lettering.
_AGUARDE_Y_RATIO = 0.935
_AGUARDE = "Aguarde..."
_AGUARDE_RGB = (90, 90, 90)


def _debug_log_path() -> Path:
    global _DEBUG_LOG
    if _DEBUG_LOG is not None:
        return _DEBUG_LOG
    home = (os.environ.get("KREUZBERG_PARSER_HOME") or "").strip()
    if home:
        base = Path(home)
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        base = Path(local) / "KiwiDown"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.cwd()
    _DEBUG_LOG = base / "splash_debug.log"
    return _DEBUG_LOG


def splash_debug(message: str, *args: object) -> None:
    """Append a splash diagnostic line (always on disk; also to logger)."""
    try:
        text = message % args if args else message
    except Exception:
        text = f"{message} {args!r}"
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n"
    try:
        with _debug_log_path().open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    try:
        logger.info("splash: %s", text)
    except Exception:
        pass


def find_splash_gif(base_dir: Path | None = None) -> Optional[Path]:
    """Locate ``kiwi_down.gif`` in installer and dev layouts.

    Handles both the correct install path (``$INSTDIR/assets/…``) and the
    legacy nested path (``$INSTDIR/assets/assets/…``) produced by older
    pynsist mappings.
    """
    here = Path(__file__).resolve().parent
    roots: list[Path] = []
    if base_dir is not None:
        base = Path(base_dir)
        roots.extend([base.parent, base, base.parent.parent])
    roots.extend(
        [
            here.parent,
            here.parent.parent,
            Path.cwd(),
        ]
    )

    relative_gif_paths = (
        Path("assets") / "kiwi_down.gif",
        Path("assets") / "assets" / "kiwi_down.gif",  # legacy nested install
        # Broken upgrade: directory named kiwi_down.gif containing the file
        Path("assets") / "kiwi_down.gif" / "kiwi_down.gif",
        Path("kiwi_down.gif"),
    )

    seen: set[str] = set()
    for root in roots:
        for rel in relative_gif_paths:
            raw = root / rel
            try:
                path = raw.resolve()
            except OSError:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            if path.is_file():
                return path
    return None


def start_early_splash(base_dir: Path | None = None) -> Optional["EarlySplash"]:
    """Start the early splash on Windows; no-op on other platforms."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    if sys.platform != "win32":
        splash_debug("early splash skipped: not Windows")
        return None
    gif = find_splash_gif(base_dir)
    if gif is None:
        splash_debug(
            "early splash skipped: kiwi_down.gif not found (base_dir=%s cwd=%s)",
            base_dir,
            Path.cwd(),
        )
        return None
    try:
        splash_debug("early splash starting gif=%s size=%s", gif, gif.stat().st_size)
        splash = EarlySplash(gif)
        splash.start()
        _INSTANCE = splash
        return splash
    except Exception as exc:
        splash_debug("early splash failed to start: %s", exc)
        logger.debug("Early splash failed to start", exc_info=True)
        return None


def early_splash_active() -> bool:
    """True while the native pre-Flet splash window is still open."""
    return _INSTANCE is not None


def close_early_splash() -> None:
    """Close the early splash if it is showing."""
    global _INSTANCE
    splash = _INSTANCE
    _INSTANCE = None
    if splash is not None:
        splash_debug("early splash close requested hwnd=%s", getattr(splash, "_hwnd", 0))
        splash.close()


class EarlySplash:
    """Borderless centered Win32 window with animated GIF + loading caption."""

    def __init__(self, gif_path: Path):
        self._gif_path = Path(gif_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._wnd_proc_ref = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="early-splash",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        hwnd = self._hwnd
        if hwnd:
            try:
                import ctypes

                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            self._run_impl()
        except Exception as exc:
            splash_debug("early splash thread crashed: %s", exc)
            logger.exception("Early splash thread crashed")

    def _run_impl(self) -> None:
        import ctypes
        import ctypes.wintypes as wt
        import io
        from PIL import Image, ImageDraw, ImageFont

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        # Per-monitor DPI so the bitmap is not tiny/clipped on notebooks.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

        LRESULT = ctypes.c_ssize_t
        HCURSOR = ctypes.c_void_p
        HICON = ctypes.c_void_p
        HBRUSH = ctypes.c_void_p
        user32.DefWindowProcW.argtypes = [
            wt.HWND,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.restype = wt.HWND
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG),
            wt.HWND,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.restype = LRESULT

        try:
            font = ImageFont.truetype("segoeui.ttf", 18)
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", 18)
            except OSError:
                font = ImageFont.load_default()

        crop = _SPLASH_SIDE_CROP_PX
        content_w = _SRC_WIDTH - (2 * crop)
        scale = _DISPLAY_WIDTH / content_w
        disp_h = max(1, int(round(_SRC_HEIGHT * scale)))
        aguarde_y = int(round(_SRC_HEIGHT * _AGUARDE_Y_RATIO * scale))

        def _compose_frame(src_img: Image.Image) -> tuple[Image.Image, int]:
            duration = max(int(src_img.info.get("duration", 40)), 20)
            frame = src_img.convert("RGBA")
            w, h = frame.size
            frame = frame.crop((crop, 0, w - crop, h))
            frame = frame.resize((_DISPLAY_WIDTH, disp_h), Image.Resampling.BILINEAR)
            # Flatten onto opaque cream so BitBlt never depends on alpha.
            canvas = Image.new("RGB", (_DISPLAY_WIDTH, disp_h), (245, 240, 228))
            canvas.paste(frame.convert("RGB"), (0, 0), frame.split()[-1])
            draw = ImageDraw.Draw(canvas)
            bbox = draw.textbbox((0, 0), _AGUARDE, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            x = (_DISPLAY_WIDTH - tw) // 2
            y = min(disp_h - th - 4, aguarde_y)
            draw.text((x, y), _AGUARDE, fill=_AGUARDE_RGB, font=font)
            return canvas, duration

        # Load the entire GIF into memory so the file on disk is not locked
        # during splash (otherwise NSIS upgrades fail to overwrite kiwi_down.gif).
        gif_bytes = self._gif_path.read_bytes()
        src = Image.open(io.BytesIO(gif_bytes))
        src.load()
        n_frames = getattr(src, "n_frames", 1)
        src.seek(0)
        first, first_duration = _compose_frame(src)
        try:
            src.close()
        except Exception:
            pass
        frames_rgb: list[Image.Image | None] = [None] * n_frames
        durations: list[int] = [40] * n_frames
        frames_rgb[0] = first
        durations[0] = first_duration
        frames_lock = threading.Lock()

        def _load_remaining() -> None:
            try:
                gif = Image.open(io.BytesIO(gif_bytes))
                gif.load()
                for i in range(1, n_frames):
                    if self._stop.is_set():
                        return
                    gif.seek(i)
                    canvas, duration = _compose_frame(gif)
                    with frames_lock:
                        frames_rgb[i] = canvas
                        durations[i] = duration
                try:
                    gif.close()
                except Exception:
                    pass
            except Exception as exc:
                splash_debug("early splash frame preload failed: %s", exc)
                logger.debug("Early splash frame preload failed", exc_info=True)

        if n_frames > 1:
            threading.Thread(
                target=_load_remaining, name="early-splash-frames", daemon=True
            ).start()

        win_w, win_h = first.size

        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        )

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE),
                ("hIcon", HICON),
                ("hCursor", HCURSOR),
                ("hbrBackground", HBRUSH),
                ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wt.HDC),
                ("fErase", wt.BOOL),
                ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL),
                ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_char * 32),
            ]

        state = {"frame": 0}

        def _current_image() -> Image.Image:
            with frames_lock:
                img = frames_rgb[state["frame"] % n_frames]
                if img is None:
                    img = frames_rgb[0]
            assert img is not None
            return img

        def _paint_to_dc(hdc: int) -> None:
            if not hdc:
                return
            img = _current_image()
            # 24bpp BGR top-down DIB — more compatible than 32bpp on some GPUs.
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = img.size[0]
            bmi.bmiHeader.biHeight = -img.size[1]
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 24
            bmi.bmiHeader.biCompression = 0  # BI_RGB
            # Rows padded to 4 bytes.
            stride = (img.size[0] * 3 + 3) & ~3
            bmi.bmiHeader.biSizeImage = stride * img.size[1]
            bits = ctypes.c_void_p()
            dib = gdi32.CreateDIBSection(
                hdc,
                ctypes.byref(bmi),
                0,
                ctypes.byref(bits),
                None,
                0,
            )
            if not dib or not bits.value:
                return
            raw = img.convert("RGB").tobytes("raw", "BGR")
            if stride == img.size[0] * 3:
                ctypes.memmove(bits, raw, len(raw))
            else:
                buf = (ctypes.c_char * (stride * img.size[1]))()
                row_bytes = img.size[0] * 3
                for y in range(img.size[1]):
                    start = y * row_bytes
                    ctypes.memmove(
                        ctypes.addressof(buf) + y * stride,
                        raw[start : start + row_bytes],
                        row_bytes,
                    )
                ctypes.memmove(bits, buf, len(buf))
            mem_dc = gdi32.CreateCompatibleDC(hdc)
            old = gdi32.SelectObject(mem_dc, dib)
            gdi32.BitBlt(hdc, 0, 0, img.size[0], img.size[1], mem_dc, 0, 0, 0x00CC0020)
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteDC(mem_dc)
            gdi32.DeleteObject(dib)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            WM_PAINT = 0x000F
            WM_ERASEBKGND = 0x0014
            WM_TIMER = 0x0113
            WM_DESTROY = 0x0002
            WM_CLOSE = 0x0010
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                try:
                    _paint_to_dc(hdc)
                finally:
                    user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            if msg == WM_TIMER:
                if self._stop.is_set():
                    user32.DestroyWindow(hwnd)
                    return 0
                nxt = (state["frame"] + 1) % n_frames
                with frames_lock:
                    if frames_rgb[nxt] is None:
                        # Hold on the latest ready frame until more arrive.
                        ready = [i for i, fr in enumerate(frames_rgb) if fr is not None]
                        nxt = ready[-1] if ready else 0
                    delay = durations[nxt]
                state["frame"] = nxt
                user32.InvalidateRect(hwnd, None, False)
                user32.SetTimer(hwnd, 1, delay, None)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.KillTimer(hwnd, 1)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wnd_proc = WNDPROC(_wnd_proc)
        self._wnd_proc_ref = wnd_proc

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "KiwiDownEarlySplash"
        wc = WNDCLASS()
        wc.style = 0x0002 | 0x0001  # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = wnd_proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = None
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        wc.hbrBackground = gdi32.GetStockObject(0)  # WHITE_BRUSH
        wc.lpszMenuName = None
        wc.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = kernel32.GetLastError()
            if err not in (1410,):  # ERROR_CLASS_ALREADY_EXISTS
                return

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        x = max(0, (screen_w - win_w) // 2)
        y = max(0, (screen_h - win_h) // 2)

        WS_POPUP = 0x80000000
        WS_VISIBLE = 0x10000000
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            class_name,
            "Kiwi Down",
            WS_POPUP | WS_VISIBLE,
            x,
            y,
            win_w,
            win_h,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            splash_debug(
                "CreateWindowExW failed last_error=%s", kernel32.GetLastError()
            )
            return
        self._hwnd = int(hwnd)
        splash_debug("early splash hwnd=%s size=%sx%s", self._hwnd, win_w, win_h)
        if self._stop.is_set():
            user32.DestroyWindow(hwnd)
            self._hwnd = 0
            return
        user32.SetTimer(hwnd, 1, durations[0], None)
        user32.InvalidateRect(hwnd, None, False)
        user32.UpdateWindow(hwnd)

        msg = wt.MSG()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._hwnd = 0
