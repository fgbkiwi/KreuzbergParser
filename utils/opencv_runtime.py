"""Bootstrap OpenCV so RapidOCR can import ``cv2`` in the Windows installer.

The pip wheel's loader pops ``sys.modules['cv2']`` and re-imports the native
``cv2.pyd``. If ``sys.path[0]`` is the parent of the ``cv2`` package (Pynsist
``pkgs/``, or ``$INSTDIR`` inserted by the launcher), that re-import finds
``cv2/__init__.py`` again and raises:

    ImportError: recursion is detected during loading of "cv2" binary extensions

``sys.OpenCV_REPLACE_SYS_PATH_0`` is the upstream workaround (insert the
folder that contains ``cv2.pyd`` at ``sys.path[0]``). A failed bootstrap also
leaves ``sys.OpenCV_LOADER`` set, so every later ``import cv2`` fails instantly.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

_cv2: Any = None


def _clear_failed_loader() -> None:
    """Undo a half-finished OpenCV bootstrap so a retry can succeed."""
    if hasattr(sys, "OpenCV_LOADER"):
        try:
            delattr(sys, "OpenCV_LOADER")
        except Exception:
            pass
    mod = sys.modules.get("cv2")
    if mod is not None and not hasattr(mod, "imread"):
        sys.modules.pop("cv2", None)


def ensure_cv2():
    """Import ``cv2`` on the calling thread. Idempotent. Raises ImportError."""
    global _cv2
    if _cv2 is not None and hasattr(_cv2, "imread"):
        return _cv2

    loaded = sys.modules.get("cv2")
    if loaded is not None and hasattr(loaded, "imread"):
        _cv2 = loaded
        return _cv2

    sys.OpenCV_REPLACE_SYS_PATH_0 = True  # type: ignore[attr-defined]
    _clear_failed_loader()

    try:
        import cv2
    except Exception:
        _clear_failed_loader()
        logger.exception("Falha ao carregar OpenCV (cv2)")
        raise

    if not hasattr(cv2, "imread"):
        _clear_failed_loader()
        raise ImportError(
            "OpenCV carregou sem extensões nativas (sem cv2.imread). "
            "Reinstale opencv-python-headless 4.x."
        )

    _cv2 = cv2
    logger.info("OpenCV %s", getattr(cv2, "__version__", "?"))
    return cv2


def cv2_or_none() -> Optional[Any]:
    """Best-effort import for optional callers (form layout / table grids)."""
    try:
        return ensure_cv2()
    except Exception:
        return None
