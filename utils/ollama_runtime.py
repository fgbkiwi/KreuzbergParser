"""Detect Ollama, check installed models, and pull Qwen VL weights.

Used by the UI for Qwen2.5-VL / Qwen3-VL presets (local, free).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

# Preset keys that use Ollama + a pullable model tag.
OLLAMA_PRESET_KEYS = frozenset({"qwen", "qwen3"})


@dataclass(frozen=True)
class OllamaStatus:
    """Result of probing Ollama for a given model tag."""

    ok: bool
    code: str  # ready | missing_binary | server_down | model_missing
    message: str
    binary: Optional[str] = None
    model: str = ""
    installed: Sequence[str] = ()


def is_ollama_preset(backend: Optional[str]) -> bool:
    return (backend or "").strip().lower() in OLLAMA_PRESET_KEYS


def find_ollama_binary() -> Optional[str]:
    """Return path to ``ollama`` / ``ollama.exe`` if on PATH or default install dirs."""
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
        ]
        for path in candidates:
            if path.is_file():
                return str(path)
    else:
        for path in (Path("/usr/local/bin/ollama"), Path.home() / ".local" / "bin" / "ollama"):
            if path.is_file():
                return str(path)
    return None


def ollama_native_base(base_url: Optional[str] = None) -> str:
    """Map OpenAI-compatible ``…/v1`` URL to Ollama native root (no trailing slash)."""
    try:
        from config import Config
    except Exception:
        Config = None  # type: ignore
    url = (base_url or getattr(Config, "VLM_BASE_URL", None) or "http://127.0.0.1:11434/v1")
    url = url.rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url or "http://127.0.0.1:11434"


def is_server_up(*, base_url: Optional[str] = None, timeout_s: float = 3.0) -> bool:
    root = ollama_native_base(base_url)
    request = urllib.request.Request(f"{root}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def list_models(*, base_url: Optional[str] = None, timeout_s: float = 8.0) -> List[str]:
    """Names from ``/api/tags`` (e.g. ``qwen3-vl:8b-instruct``)."""
    root = ollama_native_base(base_url)
    request = urllib.request.Request(f"{root}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Ollama /api/tags failed: %s", exc)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    names: List[str] = []
    for item in data.get("models") or []:
        name = (item.get("name") or item.get("model") or "").strip()
        if name:
            names.append(name)
    return names


def model_is_installed(model: str, installed: Sequence[str]) -> bool:
    """True if ``model`` is listed locally (exact tag; untagged matches any tag)."""
    want = (model or "").strip().lower()
    if not want:
        return False
    for name in installed:
        have = (name or "").strip().lower()
        if have == want:
            return True
        if ":" not in want and have.split(":", 1)[0] == want:
            return True
    return False


def probe_model(
    model: str,
    *,
    base_url: Optional[str] = None,
) -> OllamaStatus:
    """Check binary, server, and whether ``model`` is pulled."""
    model = (model or "").strip()
    binary = find_ollama_binary()
    if not binary:
        return OllamaStatus(
            ok=False,
            code="missing_binary",
            message=(
                "Ollama não encontrado. Instale com "
                "`winget install Ollama.Ollama` (Windows) ou "
                "https://ollama.com/download"
            ),
            model=model,
        )
    if not is_server_up(base_url=base_url):
        return OllamaStatus(
            ok=False,
            code="server_down",
            message=(
                "Ollama instalado, mas o serviço não responde em "
                f"{ollama_native_base(base_url)}. Abra o app Ollama "
                "ou rode `ollama serve`."
            ),
            binary=binary,
            model=model,
        )
    installed = list_models(base_url=base_url)
    if model and not model_is_installed(model, installed):
        return OllamaStatus(
            ok=False,
            code="model_missing",
            message=f"Modelo `{model}` não está instalado no Ollama.",
            binary=binary,
            model=model,
            installed=tuple(installed),
        )
    return OllamaStatus(
        ok=True,
        code="ready",
        message=f"Ollama pronto ({model or 'sem modelo'}).",
        binary=binary,
        model=model,
        installed=tuple(installed),
    )


def try_start_server(*, binary: Optional[str] = None) -> bool:
    """Best-effort: launch ``ollama serve`` detached. Returns True if spawn ok."""
    exe = binary or find_ollama_binary()
    if not exe:
        return False
    if is_server_up():
        return True
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([exe, "serve"], **kwargs)
    except OSError as exc:
        logger.warning("Falha ao iniciar ollama serve: %s", exc)
        return False
    return True


def pull_model(
    model: str,
    *,
    binary: Optional[str] = None,
    on_line: Optional[ProgressCallback] = None,
) -> tuple[bool, str]:
    """Run ``ollama pull <model>``. Returns (ok, detail)."""
    model = (model or "").strip()
    if not model:
        return False, "modelo vazio"
    exe = binary or find_ollama_binary()
    if not exe:
        return False, "binário ollama não encontrado"
    if not is_server_up():
        try_start_server(binary=exe)
        # Brief wait for cold start
        import time

        for _ in range(20):
            if is_server_up():
                break
            time.sleep(0.5)
        if not is_server_up():
            return False, "serviço Ollama não respondeu após tentativa de start"

    cmd = [exe, "pull", model]
    if on_line:
        on_line(f"Baixando `{model}` via Ollama…")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **(
                {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                if sys.platform == "win32"
                else {}
            ),
        )
    except OSError as exc:
        return False, str(exc)

    last_report = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        text = (line or "").strip()
        if not text:
            continue
        # Throttle noisy progress bars: only forward lines that change status words.
        if on_line and (text != last_report) and (
            "%" in text
            or "pulling" in text.lower()
            or "success" in text.lower()
            or "verifying" in text.lower()
            or "writing" in text.lower()
            or text.startswith("Error")
        ):
            # Collapse carriage-return style updates to last segment
            pretty = text.split("\r")[-1].strip()
            if pretty and pretty != last_report:
                on_line(pretty)
                last_report = pretty
    rc = proc.wait()
    if rc == 0:
        if on_line:
            on_line(f"Modelo `{model}` instalado.")
        return True, "ok"
    return False, f"ollama pull saiu com código {rc}"


def install_hint_windows() -> str:
    return "winget install Ollama.Ollama"
