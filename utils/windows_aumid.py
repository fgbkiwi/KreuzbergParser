"""Windows AppUserModelID helpers for correct taskbar pinning.

Flet shows its UI in a child ``flet.exe`` process. Without a Start Menu
shortcut stamped with the same Explicit AppUserModelID that ``flet.exe``
receives via ``FLET_APP_USER_MODEL_ID``, Windows pins the bare client —
relaunching it yields a blank white window (no Python backend).

See: https://github.com/flet-dev/flet/issues/5151
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_USER_MODEL_ID = "KiwiDown.App"
_START_MENU_SHORTCUT = "Kiwi Down.lnk"

_GPS_DEFAULT = 0
_GPS_READWRITE = 2
_VT_LPWSTR = 31
_COINIT_APARTMENTTHREADED = 0x2
_IID_IPROPERTY_STORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_PKEY_AUMID_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
_PKEY_AUMID_PID = 5

_GUID = None
_PROPERTYKEY = None
_PROPVARIANT = None
_COM_READY = False


def _init_com_types() -> None:
    global _GUID, _PROPERTYKEY, _PROPVARIANT
    if _GUID is not None:
        return
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("data", ctypes.c_void_p),
            ("data2", ctypes.c_ssize_t),
        ]

    _GUID = GUID
    _PROPERTYKEY = PROPERTYKEY
    _PROPVARIANT = PROPVARIANT


def _ensure_com() -> None:
    global _COM_READY
    import ctypes

    _init_com_types()
    if _COM_READY:
        return
    hr = ctypes.windll.ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if hr not in (0, 1) and (hr & 0xFFFFFFFF) != 0x80010106:
        logger.debug("CoInitializeEx returned 0x%08X", hr & 0xFFFFFFFF)
    _COM_READY = True


def _make_guid(value: str):
    _init_com_types()
    parts = value.split("-")
    g = _GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    rest = bytes.fromhex(parts[3] + parts[4])
    for i, byte in enumerate(rest):
        g.Data4[i] = byte
    return g


def _aumid_key():
    _init_com_types()
    key = _PROPERTYKEY()
    key.fmtid = _make_guid(_PKEY_AUMID_FMTID)
    key.pid = _PKEY_AUMID_PID
    return key


def set_process_app_user_model_id(aumid: str = APP_USER_MODEL_ID) -> None:
    """Set Explicit AppUserModelID on the current process (early splash, etc.)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
    except Exception:
        logger.debug("SetCurrentProcessExplicitAppUserModelID failed", exc_info=True)


def ensure_flet_env_aumid(aumid: str = APP_USER_MODEL_ID) -> None:
    """Ensure the Flet desktop child inherits our AppUserModelID."""
    if sys.platform != "win32":
        return
    os.environ["FLET_APP_USER_MODEL_ID"] = aumid


def _start_menu_dirs() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    program_data = os.environ.get("PROGRAMDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )
    if program_data:
        candidates.append(
            Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )
    return candidates


def _taskbar_pin_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (
        Path(appdata)
        / "Microsoft"
        / "Internet Explorer"
        / "Quick Launch"
        / "User Pinned"
        / "TaskBar"
    )


def _find_start_menu_shortcuts() -> list[Path]:
    found: list[Path] = []
    for folder in _start_menu_dirs():
        path = folder / _START_MENU_SHORTCUT
        if path.is_file():
            found.append(path)
    return found


def _read_shortcut_target(lnk: Path) -> str | None:
    """Best-effort .lnk target path without requiring pywin32."""
    try:
        data = lnk.read_bytes()
    except OSError:
        return None

    text = data.decode("utf-16-le", errors="ignore")
    for token in text.split("\x00"):
        t = token.strip()
        if len(t) >= 8 and t.lower().endswith(".exe") and (":\\" in t or t.startswith("\\\\")):
            return t

    ascii_text = data.decode("latin-1", errors="ignore")
    for part in ascii_text.replace("\x00", "\n").split("\n"):
        p = part.strip()
        if len(p) >= 8 and p.lower().endswith(".exe") and (":\\" in p or p.startswith("\\\\")):
            return p
    return None


def _open_property_store(path: Path, write: bool):
    import ctypes
    from ctypes import wintypes

    _ensure_com()
    iid = _make_guid(_IID_IPROPERTY_STORE)
    store = ctypes.c_void_p()
    fn = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
    fn.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.c_uint32,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    fn.restype = ctypes.HRESULT
    hr = fn(
        str(path),
        None,
        _GPS_READWRITE if write else _GPS_DEFAULT,
        ctypes.byref(iid),
        ctypes.byref(store),
    )
    if hr != 0 or not store.value:
        return None
    return store


def _vtbl(store):
    import ctypes

    obj = ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0]
    return ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))


def _release_store(store) -> None:
    import ctypes

    try:
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(_vtbl(store)[2])
        release(store)
    except Exception:
        pass


def _get_shortcut_aumid(lnk: Path) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
    except Exception:
        return None

    store = _open_property_store(lnk, write=False)
    if store is None:
        return None
    try:
        get_value = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(_PROPERTYKEY),
            ctypes.POINTER(_PROPVARIANT),
        )(_vtbl(store)[5])
        pv = _PROPVARIANT()
        hr = get_value(store, ctypes.byref(_aumid_key()), ctypes.byref(pv))
        if hr != 0 or pv.vt != _VT_LPWSTR or not pv.data:
            return None
        return ctypes.wstring_at(pv.data)
    except Exception:
        logger.debug("Read AUMID failed for %s", lnk, exc_info=True)
        return None
    finally:
        _release_store(store)


def _set_shortcut_aumid(lnk: Path, aumid: str) -> bool:
    """Write System.AppUserModel.ID onto an existing .lnk via IPropertyStore."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
    except Exception:
        return False

    store = _open_property_store(lnk, write=True)
    if store is None:
        return False
    buf = ctypes.create_unicode_buffer(aumid)
    try:
        vtbl = _vtbl(store)
        set_value = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(_PROPERTYKEY),
            ctypes.POINTER(_PROPVARIANT),
        )(vtbl[6])
        commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtbl[7])
        pv = _PROPVARIANT()
        pv.vt = _VT_LPWSTR
        pv.data = ctypes.cast(buf, ctypes.c_void_p)
        if set_value(store, ctypes.byref(_aumid_key()), ctypes.byref(pv)) != 0:
            return False
        return commit(store) == 0
    except Exception:
        logger.debug("AUMID stamp failed for %s", lnk, exc_info=True)
        return False
    finally:
        _release_store(store)


def _is_bare_flet_pin(lnk: Path) -> bool:
    target = (_read_shortcut_target(lnk) or "").replace("/", "\\").lower()
    if not target.endswith("\\flet.exe"):
        return False
    return "kiwi down" in target and "flet_desktop" in target


def _replace_bad_taskbar_pins(good_shortcut: Path, aumid: str) -> int:
    """Replace taskbar pins that launch bare flet.exe with the Start Menu lnk."""
    pin_dir = _taskbar_pin_dir()
    if pin_dir is None or not pin_dir.is_dir():
        return 0

    replaced = 0
    dest = pin_dir / _START_MENU_SHORTCUT
    try:
        pins = list(pin_dir.glob("*.lnk"))
    except OSError:
        return 0

    for pin in pins:
        try:
            if not _is_bare_flet_pin(pin):
                continue
            shutil.copy2(good_shortcut, dest)
            if _get_shortcut_aumid(dest) != aumid:
                _set_shortcut_aumid(dest, aumid)
            if pin.resolve() != dest.resolve():
                pin.unlink(missing_ok=True)
            replaced += 1
            logger.info("Replaced bare flet.exe taskbar pin with Kiwi Down shortcut")
        except OSError:
            logger.debug("Could not replace taskbar pin %s", pin, exc_info=True)
    return replaced


def ensure_windows_taskbar_identity(aumid: str = APP_USER_MODEL_ID) -> None:
    """Stamp Start Menu AUMID and repair bare-flet taskbar pins when possible."""
    if sys.platform != "win32":
        return

    ensure_flet_env_aumid(aumid)
    set_process_app_user_model_id(aumid)

    good: Path | None = None
    for lnk in _find_start_menu_shortcuts():
        current = _get_shortcut_aumid(lnk)
        if current != aumid:
            if _set_shortcut_aumid(lnk, aumid):
                logger.info("Stamped AppUserModelID=%s on %s", aumid, lnk)
            else:
                logger.debug("Could not stamp AppUserModelID on %s", lnk)
        good = lnk

    if good is not None:
        _replace_bad_taskbar_pins(good, aumid)
