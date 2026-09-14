"""Replace the embedded icon of flet.exe (Windows) with a custom .ico.

Used by the Pynsist build so the taskbar/pin uses KiwiDown.ico instead of
the default Flet client icon. Works on a loose exe or inside flet-windows.zip.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path


RT_ICON = 3
RT_GROUP_ICON = 14
LANG_NEUTRAL = 1033


def _read_ico(path: Path) -> tuple[bytes, list[bytes]]:
    data = path.read_bytes()
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        raise ValueError(f"Not a Windows .ico file: {path}")
    count = struct.unpack_from("<H", data, 4)[0]
    if count < 1:
        raise ValueError(f"ICO has no images: {path}")

    images: list[bytes] = []
    entries: list[tuple[int, int, int, int, int, int]] = []
    for i in range(count):
        off = 6 + i * 16
        width, height, colors, _reserved, planes, bitcount, bytes_in_res, img_off = (
            struct.unpack_from("<BBBBHHII", data, off)
        )
        img = data[img_off : img_off + bytes_in_res]
        if len(img) != bytes_in_res:
            raise ValueError(f"Truncated ICO image data in {path}")
        images.append(img)
        entries.append((width, height, colors, planes, bitcount, bytes_in_res))

    # GRPICONDIR + GRPICONDIRENTRY (iconid starts at 1)
    group = struct.pack("<HHH", 0, 1, count)
    for idx, (width, height, colors, planes, bitcount, bytes_in_res) in enumerate(
        entries, start=1
    ):
        group += struct.pack(
            "<BBBBHHIH",
            width,
            height,
            colors,
            0,
            planes,
            bitcount,
            bytes_in_res,
            idx,
        )
    return group, images


def patch_exe_icon(exe_path: Path, ico_path: Path) -> None:
    """Overwrite flet.exe icon resources (same IDs used by ``flet pack``)."""
    if sys.platform != "win32":
        raise RuntimeError("Icon resource patching is Windows-only")

    import ctypes
    from ctypes import wintypes

    group, images = _read_ico(ico_path)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    BeginUpdateResourceW = kernel32.BeginUpdateResourceW
    BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    BeginUpdateResourceW.restype = wintypes.HANDLE

    # Type/name accept MAKEINTRESOURCE integers via LPVOID.
    UpdateResourceW = kernel32.UpdateResourceW
    UpdateResourceW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.WORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    UpdateResourceW.restype = wintypes.BOOL

    EndUpdateResourceW = kernel32.EndUpdateResourceW
    EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    EndUpdateResourceW.restype = wintypes.BOOL

    handle = BeginUpdateResourceW(str(exe_path), False)
    if not handle:
        raise OSError(
            ctypes.get_last_error(), f"BeginUpdateResource failed for {exe_path}"
        )

    # Flutter/Flet ships the app icon as RT_GROUP_ICON #101 (see flet pack).
    group_id = 101
    try:
        group_buf = ctypes.create_string_buffer(group)
        ok = UpdateResourceW(
            handle,
            RT_GROUP_ICON,
            group_id,
            LANG_NEUTRAL,
            group_buf,
            len(group),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "UpdateResource RT_GROUP_ICON failed")

        for icon_id, image in enumerate(images, start=1):
            img_buf = ctypes.create_string_buffer(image)
            ok = UpdateResourceW(
                handle,
                RT_ICON,
                icon_id,
                LANG_NEUTRAL,
                img_buf,
                len(image),
            )
            if not ok:
                raise OSError(
                    ctypes.get_last_error(),
                    f"UpdateResource RT_ICON {icon_id} failed",
                )

        if not EndUpdateResourceW(handle, False):
            raise OSError(ctypes.get_last_error(), "EndUpdateResource failed")
        handle = None
    finally:
        if handle:
            EndUpdateResourceW(handle, True)


def _find_flet_exe_in_dir(root: Path) -> Path:
    direct = root / "flet" / "flet.exe"
    if direct.is_file():
        return direct
    matches = sorted(root.rglob("flet.exe"))
    if not matches:
        raise FileNotFoundError(f"flet.exe not found under {root}")
    return matches[0]


def patch_zip_icon(zip_path: Path, ico_path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="kiwi_flet_icon_") as tmp:
        tmp_dir = Path(tmp)
        extract_dir = tmp_dir / "client"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        exe = _find_flet_exe_in_dir(extract_dir)
        patch_exe_icon(exe, ico_path)

        out_zip = tmp_dir / "flet-windows.zip"
        # Preserve top-level layout expected by flet_desktop (usually "flet/...").
        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in extract_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(extract_dir).as_posix())

        shutil.move(str(out_zip), str(zip_path))
        return str(exe.relative_to(extract_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--icon", required=True, type=Path, help="Path to .ico file")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exe", type=Path, help="Path to flet.exe")
    group.add_argument("--zip", type=Path, help="Path to flet-windows.zip")
    args = parser.parse_args(argv)

    ico = args.icon.resolve()
    if not ico.is_file():
        print(f"ERROR: icon not found: {ico}", file=sys.stderr)
        return 1

    if args.exe:
        exe = args.exe.resolve()
        if not exe.is_file():
            print(f"ERROR: exe not found: {exe}", file=sys.stderr)
            return 1
        patch_exe_icon(exe, ico)
        print(f"Patched icon on {exe}")
        return 0

    zip_path = args.zip.resolve()
    if not zip_path.is_file():
        print(f"ERROR: zip not found: {zip_path}", file=sys.stderr)
        return 1
    rel = patch_zip_icon(zip_path, ico)
    print(f"Patched icon on {rel} inside {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
