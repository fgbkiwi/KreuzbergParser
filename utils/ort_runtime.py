"""
ONNX Runtime bootstrap for PaddleOCR GPU.

Kreuzberg 4.10.2 embeds a CPU-only ORT and skips ORT_DYLIB_PATH, so CUDA
PaddleOCR runs through Python onnxruntime-gpu (RapidOCR) instead. This
module puts PyTorch CUDA/cuDNN DLLs on the loader path and preloads them
before any InferenceSession is created.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

_cuda_ready: Optional[bool] = None


def _unique_existing_dirs(candidates: Iterable[Path]) -> List[Path]:
    seen = set()
    out: List[Path] = []
    for raw in candidates:
        try:
            path = raw.resolve()
        except OSError:
            continue
        if not path.is_dir():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _nvidia_lib_dirs() -> List[Path]:
    """CUDA/cuDNN dirs shipped by nvidia-* wheels and PyTorch."""
    candidates: List[Path] = []
    try:
        import nvidia  # type: ignore

        roots = getattr(nvidia, "__path__", None) or []
        for root in roots:
            base = Path(root)
            if not base.is_dir():
                continue
            for sub in base.iterdir():
                if not sub.is_dir():
                    continue
                for name in ("bin", "lib", "lib64"):
                    candidates.append(sub / name)
    except Exception:
        pass

    try:
        import torch

        torch_dir = Path(torch.__file__).resolve().parent
        candidates.append(torch_dir / "lib")
        candidates.append(torch_dir / "bin")
    except Exception:
        pass

    try:
        import onnxruntime  # type: ignore

        paths = getattr(onnxruntime, "__path__", None)
        if paths:
            candidates.append(Path(list(paths)[0]) / "capi")
    except Exception:
        pass

    for env_key in ("CUDA_PATH", "CUDA_HOME"):
        root = (os.environ.get(env_key) or "").strip()
        if not root:
            continue
        base = Path(root)
        for name in ("bin", "lib", "lib64"):
            candidates.append(base / name)

    return _unique_existing_dirs(candidates)


def _add_library_dirs(dirs: List[Path]) -> None:
    extra = [str(path) for path in dirs]
    if not extra:
        return

    if sys.platform == "win32":
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(
            extra + ([current] if current else [])
        )
        add_dll = getattr(os, "add_dll_directory", None)
        if callable(add_dll):
            for path in extra:
                try:
                    add_dll(path)
                except OSError:
                    pass
        return

    env_key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    current = os.environ.get(env_key, "")
    os.environ[env_key] = os.pathsep.join(extra + ([current] if current else []))


def _capi_dir() -> Optional[Path]:
    try:
        import onnxruntime  # type: ignore
    except ImportError:
        return None
    paths = getattr(onnxruntime, "__path__", None)
    if not paths:
        return None
    capi = Path(list(paths)[0]) / "capi"
    return capi if capi.is_dir() else None


def _find_ort_library(capi: Path) -> Optional[Path]:
    names = (
        "onnxruntime.dll",
        "libonnxruntime.so",
        "libonnxruntime.dylib",
    )
    for name in names:
        candidate = capi / name
        if candidate.is_file():
            return candidate
    extras = sorted(capi.glob("libonnxruntime.so.*"))
    return extras[0] if extras else None


def preload_cuda_runtime() -> None:
    """Expose CUDA/cuDNN from nvidia/torch wheels to ORT's dynamic loader."""
    dirs = _nvidia_lib_dirs()
    _add_library_dirs(dirs)
    if dirs:
        logger.debug("CUDA library dirs: %s", dirs)
    try:
        import onnxruntime  # type: ignore

        preload = getattr(onnxruntime, "preload_dlls", None)
        if callable(preload):
            preload(cuda=True, cudnn=True, msvc=sys.platform == "win32")
    except Exception:
        logger.debug("onnxruntime.preload_dlls skipped", exc_info=True)

    capi = _capi_dir()
    if capi is None or sys.platform != "win32":
        return
    try:
        import ctypes

        for name in (
            "onnxruntime.dll",
            "onnxruntime_providers_shared.dll",
            "onnxruntime_providers_cuda.dll",
        ):
            path = capi / name
            if path.is_file():
                ctypes.WinDLL(str(path))
    except OSError:
        logger.debug("ctypes preload of ORT CUDA provider skipped", exc_info=True)


def ort_cuda_available() -> bool:
    """True when the Python onnxruntime reports a CUDA execution provider."""
    try:
        import onnxruntime  # type: ignore

        providers = [str(p) for p in onnxruntime.get_available_providers()]
        return any("CUDA" in p.upper() for p in providers)
    except Exception:
        return False


def ensure_ort_dylib_path() -> bool:
    """Point ORT_DYLIB_PATH at GPU onnxruntime (used by non-bundled ORT loaders)."""
    existing = (os.environ.get("ORT_DYLIB_PATH") or "").strip()
    if existing:
        path = Path(existing)
        if path.exists():
            return True
        logger.warning("ORT_DYLIB_PATH=%s does not exist", existing)

    capi = _capi_dir()
    if capi is None:
        logger.debug("onnxruntime-gpu not installed")
        return False

    library = _find_ort_library(capi)
    target = str(library if library is not None else capi)
    os.environ["ORT_DYLIB_PATH"] = target
    logger.info("ORT_DYLIB_PATH=%s", target)
    return True


def prepare_paddle_gpu_runtime() -> bool:
    """
    Load CUDA libs and confirm Python onnxruntime-gpu has CUDA EP.

    Must run before RapidOCR creates InferenceSessions.
    """
    global _cuda_ready
    if _cuda_ready is True:
        return True
    try:
        import torch  # noqa: F401 — importing torch preloads CUDA DLLs
    except Exception:
        logger.debug("torch import skipped during ORT bootstrap", exc_info=True)
    preload_cuda_runtime()
    ensure_ort_dylib_path()
    ready = ort_cuda_available()
    if ready:
        _cuda_ready = True
        logger.info("onnxruntime-gpu CUDAExecutionProvider is available")
    else:
        logger.warning(
            "onnxruntime-gpu CUDAExecutionProvider is not available. "
            "PaddleOCR GPU requires: pip install onnxruntime-gpu>=1.27"
        )
    return ready


def log_ort_status() -> None:
    ready = prepare_paddle_gpu_runtime()
    capi = _capi_dir()
    if capi is None:
        logger.info(
            "ONNX Runtime: onnxruntime-gpu not installed "
            "(needed for PaddleOCR GPU — see GPU_SETUP.md)"
        )
        return
    logger.info(
        "ONNX Runtime: capi=%s CUDA EP=%s platform=%s ORT_DYLIB_PATH=%s",
        capi,
        ready,
        sys.platform,
        os.environ.get("ORT_DYLIB_PATH") or "-",
    )
