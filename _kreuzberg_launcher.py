"""Launcher resiliente para o instalador Pynsist."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
import traceback
from pathlib import Path

APP_NAME = "Kiwi Down"


def _isolate_from_user_site() -> None:
    """Ignore %APPDATA%\\Python site-packages (breaks bundled transformers/hub)."""
    os.environ["PYTHONNOUSERSITE"] = "1"
    try:
        import site

        site.ENABLE_USER_SITE = False
    except Exception:
        pass

    drop_markers = (
        f"python{sys.version_info.major}{sys.version_info.minor}",
        "roaming\\python",
        "appdata\\roaming\\python",
    )
    cleaned: list[str] = []
    for entry in sys.path:
        low = entry.replace("/", "\\").lower()
        if any(marker in low for marker in drop_markers) and "site-packages" in low:
            continue
        cleaned.append(entry)
    sys.path[:] = cleaned


def _add_dll_directories(base_dir: Path) -> None:
    """Register native DLL search paths for Python 3.8+ on Windows.

    Pynsist copies packages under ``pkgs/``, but wheels like NumPy keep OpenBLAS
    etc. in sibling ``*.libs`` folders. Without ``os.add_dll_directory``, imports
    fail with ``DLL load failed while importing _multiarray_umath``.

    Also walks nested ``*.libs/*.libs`` layouts caused by older installer maps.
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
    # Any delvewheel-style *.libs next to packages (and one nested level).
    for parent in (base_dir, base_dir.parent):
        if not parent.is_dir():
            continue
        for libs_dir in sorted(parent.glob("*.libs")):
            candidates.append(libs_dir)
            candidates.extend(sorted(libs_dir.glob("*.libs")))

    # Any directory under *.libs that actually contains DLLs.
    for parent in (base_dir, base_dir.parent):
        if not parent.is_dir():
            continue
        for libs_dir in parent.glob("*.libs"):
            try:
                for dll in libs_dir.rglob("*.dll"):
                    candidates.append(dll.parent)
            except OSError:
                continue

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
    app_dir = local_appdata / "KiwiDown"
    app_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("KREUZBERG_PARSER_HOME", str(app_dir))
    # Compatibilidade com instalacoes antigas / docs.
    os.environ.setdefault("KIWI_DOWN_HOME", str(app_dir))
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
        namespace = runpy.run_path(str(fallback_script), run_name="__kiwi_down__")
        if "main" in namespace and callable(namespace["main"]):
            return namespace["main"]

    raise ModuleNotFoundError("No module named 'main'")


def _configure_flet_desktop(base_dir: Path) -> None:
    """Point Flet at a bundled desktop client when shipped with the installer."""
    candidates = [
        base_dir / "flet_desktop" / "app" / "flet",
        base_dir.parent / "flet_desktop" / "app" / "flet",
        base_dir / "flet_desktop" / "view",
        base_dir.parent / "flet_desktop" / "view",
    ]
    for path in candidates:
        if (path / "flet.exe").is_file():
            os.environ.setdefault("FLET_VIEW_PATH", str(path))
            return


def _harden_flet_desktop_bootstrap() -> None:
    """Never attempt pip/uv installs inside a Program Files install."""
    try:
        import flet.utils.pip as flet_pip
    except Exception:
        return

    def _ensure() -> None:
        try:
            import flet.version as fv
            import flet_desktop.version as fdv
        except Exception as exc:  # noqa: BLE001 - surface as startup error
            raise RuntimeError(
                "Pacote flet-desktop ausente ou incompleto no instalador "
                f"(deps como 'rich' precisam estar empacotadas): {exc}"
            ) from exc
        if fdv.version and fv.flet_version and fdv.version != fv.flet_version:
            raise RuntimeError(
                f"Versao flet-desktop ({fdv.version}) difere do flet "
                f"({fv.flet_version}). Regenere o instalador."
            )

    flet_pip.ensure_flet_desktop_package_installed = _ensure  # type: ignore[assignment]


def _suppress_console_window() -> None:
    """Hide a console if this process was started as a GUI app (pythonw).

    Keeps the console when the user intentionally launched from a terminal
    (stdout is a TTY), so ``python main.py`` debugging still works.
    """
    if sys.platform != "win32":
        return
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            return
    except Exception:
        pass
    # pythonw / redirected stdout: if a console is attached, hide and detach.
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    _suppress_console_window()
    _isolate_from_user_site()
    _add_dll_directories(base_dir)
    # Writable home first so splash diagnostics land next to other app logs.
    log_dir = _set_writable_workdir()
    # Stamp Start Menu AppUserModelID + repair bare-flet taskbar pins so
    # "Pin to taskbar" relaunches the Python launcher, not flet.exe alone
    # (blank white window). Also sets FLET_APP_USER_MODEL_ID for the child.
    if sys.platform == "win32":
        try:
            # Prefer bundled helper; fall back if pkgs layout differs.
            from utils.windows_aumid import ensure_windows_taskbar_identity

            ensure_windows_taskbar_identity()
        except Exception:
            os.environ.setdefault("FLET_APP_USER_MODEL_ID", "KiwiDown.App")
    # Show branding immediately — before Torch/Kreuzberg/Flet imports.
    close_early_splash = lambda: None  # noqa: E731 — replaced on success
    try:
        from utils.early_splash import close_early_splash, splash_debug, start_early_splash

        splash_debug(
            "launcher start base_dir=%s cwd=%s python=%s",
            base_dir,
            Path.cwd(),
            sys.executable,
        )
        start_early_splash(base_dir)
    except Exception as exc:
        try:
            from utils.early_splash import splash_debug

            splash_debug("launcher early splash import/start failed: %s", exc)
        except Exception:
            pass
    _configure_flet_desktop(base_dir)
    _harden_flet_desktop_bootstrap()
    try:
        from utils.flet_ui import patch_flet_desktop_no_console

        patch_flet_desktop_no_console()
    except Exception:
        pass

    try:
        app_main = _resolve_app_main(base_dir)
        app_main()
    except Exception:
        try:
            close_early_splash()
        except Exception:
            pass
        err = traceback.format_exc()
        log_path = log_dir / "startup_error.log"
        log_path.write_text(err, encoding="utf-8")
        _show_error_dialog(
            f"Falha ao iniciar o {APP_NAME}.\n\n"
            f"Detalhes salvos em:\n{log_path}"
        )
        raise


if __name__ == "__main__":
    main()
