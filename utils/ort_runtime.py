"""
ONNX Runtime bootstrap for Kreuzberg native PaddleOCR GPU.

Docs: https://docs.kreuzberg.dev/getting-started/installation/#gpu-acceleration
  - Kreuzberg bundles CPU-only ONNX Runtime.
  - GPU: pip/uv install onnxruntime-gpu and set ORT_DYLIB_PATH to the
    GPU library (Windows: .../onnxruntime/capi/onnxruntime.dll).

CUDA/cuDNN from nvidia-* / PyTorch wheels must be on the loader path
*before* onnxruntime or kreuzberg are imported. Do not import torch
first — that skips onnxruntime.preload_dlls().
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_cuda_ready: Optional[bool] = None
_CUDA_PROVIDER = "CUDAExecutionProvider"


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


def _package_search_paths(name: str) -> List[Path]:
    """Locate a package directory without importing it (avoids torch-before-ORT)."""
    spec = importlib.util.find_spec(name)
    if spec is None:
        return []
    paths: List[Path] = []
    for loc in spec.submodule_search_locations or []:
        paths.append(Path(loc))
    if spec.origin:
        paths.append(Path(spec.origin).resolve().parent)
    return paths


def _nvidia_lib_dirs() -> List[Path]:
    """CUDA/cuDNN dirs shipped by nvidia-* wheels and PyTorch."""
    candidates: List[Path] = []
    for root in _package_search_paths("nvidia"):
        if not root.is_dir():
            continue
        for sub in root.iterdir():
            if not sub.is_dir():
                continue
            for name in ("bin", "lib", "lib64"):
                candidates.append(sub / name)

    for torch_dir in _package_search_paths("torch"):
        candidates.append(torch_dir / "lib")
        candidates.append(torch_dir / "bin")

    for ort_dir in _package_search_paths("onnxruntime"):
        candidates.append(ort_dir / "capi")

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
    for ort_dir in _package_search_paths("onnxruntime"):
        capi = ort_dir / "capi"
        if capi.is_dir():
            return capi
    return None


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
        return _CUDA_PROVIDER in providers
    except Exception:
        return False


def session_uses_cuda(session: Any) -> bool:
    """True when an InferenceSession is actually bound to CUDA (not CPU fallback)."""
    try:
        providers = [str(p) for p in session.get_providers()]
    except Exception:
        return False
    return bool(providers) and providers[0] == _CUDA_PROVIDER


def ensure_ort_dylib_path() -> bool:
    """
    Point ORT_DYLIB_PATH at the GPU ONNX Runtime shared library.

    Kreuzberg docs (Windows): set ORT_DYLIB_PATH to onnxruntime.dll.
    Python docs also mention the capi/ directory; the file path is what
    the `ort` crate loads. Prefer the DLL/SO/DYLIB over a directory.
    """
    existing = (os.environ.get("ORT_DYLIB_PATH") or "").strip()
    if existing:
        path = Path(existing)
        if path.is_file():
            logger.info("ORT_DYLIB_PATH=%s", path)
            return True
        if path.is_dir():
            library = _find_ort_library(path)
            if library is not None:
                os.environ["ORT_DYLIB_PATH"] = str(library)
                logger.info("ORT_DYLIB_PATH=%s (resolved from directory)", library)
                return True
            logger.warning(
                "ORT_DYLIB_PATH=%s is a directory without onnxruntime.dll/.so/.dylib",
                existing,
            )
        else:
            logger.warning("ORT_DYLIB_PATH=%s does not exist", existing)

    capi = _capi_dir()
    if capi is None:
        logger.debug("onnxruntime-gpu not installed")
        return False

    library = _find_ort_library(capi)
    if library is None:
        logger.warning("onnxruntime-gpu capi has no onnxruntime shared library: %s", capi)
        return False
    os.environ["ORT_DYLIB_PATH"] = str(library)
    logger.info("ORT_DYLIB_PATH=%s", library)
    return True


def prepare_paddle_gpu_runtime() -> bool:
    """
    Load CUDA libs, set ORT_DYLIB_PATH, confirm onnxruntime-gpu has CUDA EP.

    Must run before `import kreuzberg` so the native bindings can see
    ORT_DYLIB_PATH. Does not import torch first.
    """
    global _cuda_ready
    if _cuda_ready is True:
        return True
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


def ort_runtime_snapshot() -> Dict[str, Any]:
    """Facts for diagnosing Kreuzberg native CUDA vs Python onnxruntime-gpu."""
    capi = _capi_dir()
    cuda_lib = None
    if capi is not None:
        for name in (
            "onnxruntime_providers_cuda.dll",
            "libonnxruntime_providers_cuda.so",
            "libonnxruntime_providers_cuda.dylib",
        ):
            candidate = capi / name
            if candidate.is_file():
                cuda_lib = str(candidate)
                break
    version = ""
    providers: List[str] = []
    try:
        import onnxruntime  # type: ignore

        version = str(getattr(onnxruntime, "__version__", "") or "")
        providers = [str(p) for p in onnxruntime.get_available_providers()]
    except Exception as exc:
        version = f"import_failed:{exc}"
    return {
        "onnxruntime_version": version,
        "onnxruntime_providers": providers,
        "ORT_DYLIB_PATH": os.environ.get("ORT_DYLIB_PATH") or "",
        "capi_dir": str(capi) if capi else "",
        "cuda_provider_library": cuda_lib or "",
        "platform": sys.platform,
    }


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
