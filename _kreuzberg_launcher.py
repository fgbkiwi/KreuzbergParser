"""Launcher resiliente para o instalador Pynsist."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
import traceback
from pathlib import Path

APP_NAME = "KreuzbergParser"


def _add_dll_directories(base_dir: Path) -> None:
    """Register native DLL search paths for Python 3.8+ on Windows.

    Pynsist copies packages under ``pkgs/``, but wheels like NumPy keep OpenBLAS
    etc. in sibling ``*.libs`` folders. Without ``os.add_dll_directory``, imports
    fail with ``DLL load failed while importing _multiarray_umath``.
    """
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = [
        base_dir,
        base_dir.parent,
        base_dir / "numpy.libs",
        base_dir / "scipy.libs",
        base_dir / "pandas.libs",
        base_dir / "shapely.libs",
        base_dir / "cv2",
        base_dir / "torch" / "lib",
        base_dir / "onnxruntime" / "capi",
        base_dir / "flet" / "bin",
    ]
    # Any other delvewheel-style *.libs next to packages.
    for parent in (base_dir, base_dir.parent):
        if parent.is_dir():
            candidates.extend(sorted(parent.glob("*.libs")))

    seen: set[str] = set()
    path_prefix: list[str] = []
    for raw in candidates:
        try:
            path = raw.resolve()
        except OSError:
            continue
        if not path.is_dir():
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            os.add_dll_directory(str(path))
        except OSError:
            continue
        path_prefix.append(str(path))

    if path_prefix:
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(path_prefix + ([current] if current else []))


def _set_writable_workdir() -> Path:
    """Usa pasta do usuário para logs/cache em vez de Program Files."""
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    app_dir = local_appdata / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("KREUZBERG_PARSER_HOME", str(app_dir))
    os.chdir(app_dir)
    return app_dir


def _show_error_dialog(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
    except Exception:
        pass


def _resolve_app_main(base_dir: Path):
    """Resolve a função main do app em layouts diferentes do instalador."""
    search_paths = [base_dir, base_dir.parent]
    for path in search_paths:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    try:
        module = importlib.import_module("main")
        return module.main
    except ModuleNotFoundError:
        pass

    fallback_script = base_dir.parent / "main.py"
    if fallback_script.exists():
        namespace = runpy.run_path(str(fallback_script), run_name="__kreuzberg_parser__")
        if "main" in namespace and callable(namespace["main"]):
            return namespace["main"]

    raise ModuleNotFoundError("No module named 'main'")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    _add_dll_directories(base_dir)
    log_dir = _set_writable_workdir()

    try:
        app_main = _resolve_app_main(base_dir)
        app_main()
    except Exception:
        err = traceback.format_exc()
        log_path = log_dir / "startup_error.log"
        log_path.write_text(err, encoding="utf-8")
        _show_error_dialog(
            "Falha ao iniciar o KreuzbergParser.\n\n"
            f"Detalhes salvos em:\n{log_path}"
        )
        raise


if __name__ == "__main__":
    main()
