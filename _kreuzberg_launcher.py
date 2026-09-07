"""Launcher resiliente para o instalador Pynsist."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
import traceback
from pathlib import Path

APP_NAME = "KreuzbergParser"


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
