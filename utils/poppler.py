"""
Ensure Poppler CLI tools (pdftotext, pdfinfo, pdfimages) are available.

Page classification depends on these binaries. On Windows they are not
usually on PATH, so we cache a project-local copy (same idea as tessdata/).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_REQUIRED_TOOLS = ("pdftotext", "pdfinfo")
_WINDOWS_RELEASE = "26.02.0-0"
_WINDOWS_ZIP_URL = (
    "https://github.com/oschwartz10612/poppler-windows/releases/download/"
    f"v{_WINDOWS_RELEASE}/Release-{_WINDOWS_RELEASE}.zip"
)

_bin_dir: Path | None = None
_ensured = False


def ensure_poppler(cache_dir: Path | str | None = None) -> Path:
    """
    Locate Poppler binaries, downloading a Windows build if needed.

    Prepends the bin directory to PATH and returns it.
    """
    global _bin_dir, _ensured

    if _ensured and _bin_dir is not None and _tools_present(_bin_dir):
        return _bin_dir

    target_dir = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir is not None
        else Path(__file__).resolve().parent.parent / "poppler"
    )

    found = _find_existing_bin_dir(target_dir)
    if found is None and sys.platform == "win32":
        logger.info("Poppler não encontrado; baixando binários para %s", target_dir)
        _download_windows_poppler(target_dir)
        found = _find_existing_bin_dir(target_dir)

    if found is None:
        raise RuntimeError(_missing_message())

    _prepend_path(found)
    _bin_dir = found
    _ensured = True
    logger.info("Poppler bin=%s", found)
    return found


def poppler_available() -> bool:
    """Return True when pdftotext and pdfinfo can be resolved."""
    try:
        ensure_poppler()
    except Exception:
        logger.debug("Poppler indisponível", exc_info=True)
        return False
    return all(poppler_tool(name) is not None for name in _REQUIRED_TOOLS)


def poppler_tool(name: str) -> str | None:
    """Absolute path to a Poppler executable, or None if missing."""
    if not _ensured:
        try:
            ensure_poppler()
        except Exception:
            logger.debug("Poppler indisponível", exc_info=True)
    if _bin_dir is not None:
        candidate = _executable(_bin_dir, name)
        if candidate is not None:
            return str(candidate)
    found = shutil.which(name)
    return found


def subprocess_kwargs() -> dict:
    """Extra kwargs so GUI runs do not flash a console on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _tools_present(bin_dir: Path) -> bool:
    return all(_executable(bin_dir, name) is not None for name in _REQUIRED_TOOLS)


def _executable(bin_dir: Path, name: str) -> Path | None:
    names = (f"{name}.exe", name) if sys.platform == "win32" else (name, f"{name}.exe")
    for filename in names:
        path = bin_dir / filename
        if path.is_file():
            return path
    return None


def _find_existing_bin_dir(cache_dir: Path) -> Path | None:
    which_text = shutil.which("pdftotext")
    which_info = shutil.which("pdfinfo")
    if which_text and which_info:
        return Path(which_text).resolve().parent

    env_dirs = []
    for key in ("POPPLER_PATH", "POPPLER_BIN"):
        raw = os.environ.get(key, "").strip()
        if raw:
            env_dirs.append(Path(raw))

    for candidate in (*env_dirs, cache_dir, *_common_install_dirs()):
        resolved = _bin_dir_from(candidate)
        if resolved is not None:
            return resolved
    return None


def _bin_dir_from(root: Path) -> Path | None:
    if not root:
        return None
    root = root.expanduser()
    if not root.exists():
        return None

    if root.is_file():
        return None

    direct = [root, root / "bin", root / "Library" / "bin"]
    nested: list[Path] = []
    try:
        for child in root.iterdir():
            if child.is_dir():
                nested.append(child / "bin")
                nested.append(child / "Library" / "bin")
    except OSError:
        return None

    for candidate in (*direct, *nested):
        if _tools_present(candidate):
            return candidate.resolve()
    return None


def _common_install_dirs() -> Iterable[Path]:
    home = Path.home()
    yield home / "scoop" / "apps" / "poppler" / "current" / "bin"
    yield home / "scoop" / "apps" / "poppler" / "current" / "Library" / "bin"
    if sys.platform == "win32":
        yield Path(r"C:\Program Files\poppler\Library\bin")
        yield Path(r"C:\Program Files\poppler\bin")
        yield Path(r"C:\Program Files (x86)\poppler\Library\bin")
        yield Path(r"C:\poppler\Library\bin")
        yield Path(r"C:\poppler\bin")
        yield Path(sys.prefix) / "Library" / "bin"
    else:
        yield Path("/usr/bin")
        yield Path("/usr/local/bin")
        yield Path("/opt/homebrew/bin")


def _prepend_path(bin_dir: Path) -> None:
    bin_str = str(bin_dir)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if parts and Path(parts[0]) == bin_dir:
        return
    os.environ["PATH"] = os.pathsep.join([bin_str, *parts]) if parts else bin_str


def _download_windows_poppler(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f"Release-{_WINDOWS_RELEASE}.zip"
    tmp_path = zip_path.with_suffix(".zip.partial")
    try:
        logger.info("Baixando %s ...", _WINDOWS_ZIP_URL)
        request = urllib.request.Request(
            _WINDOWS_ZIP_URL,
            headers={"User-Agent": "KreuzbergParser"},
        )
        with urllib.request.urlopen(request, timeout=120) as response, tmp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        tmp_path.replace(zip_path)
        logger.info("Extraindo Poppler em %s", cache_dir)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(cache_dir)
    except (urllib.error.URLError, OSError, zipfile.BadZipFile) as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Falha ao baixar o Poppler para Windows. "
            f"Baixe {_WINDOWS_ZIP_URL} e extraia em {cache_dir}"
        ) from exc
    finally:
        if zip_path.exists():
            zip_path.unlink(missing_ok=True)


def _missing_message() -> str:
    if sys.platform == "win32":
        return (
            "Poppler (pdftotext/pdfinfo) é necessário para classificar páginas. "
            "O download automático falhou. Instale o Poppler ou extraia o zip "
            f"oficial em poppler/: {_WINDOWS_ZIP_URL}"
        )
    if sys.platform == "darwin":
        return (
            "Poppler (pdftotext/pdfinfo) é necessário para classificar páginas. "
            "Instale com: brew install poppler"
        )
    return (
        "Poppler (pdftotext/pdfinfo) é necessário para classificar páginas. "
        "Instale com: sudo apt install poppler-utils"
    )
