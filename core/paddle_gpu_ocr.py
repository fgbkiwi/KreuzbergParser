"""
Official PaddleOCR GPU (ONNX Runtime CUDA).

Kreuzberg 4.9.4+ applies AccelerationConfig(provider="cuda"), but the 4.10
wheel still bundles a CPU-only ONNX Runtime and ignores the system
onnxruntime-gpu. This module runs PaddleOCR 3.x with engine='onnxruntime'
and CUDAExecutionProvider only — no silent CPU fallback.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Iterable, List, Optional, Tuple

import numpy as np

from utils.ort_runtime import prepare_paddle_gpu_runtime, session_uses_cuda

logger = logging.getLogger(__name__)

_CUDA_PROVIDER = "CUDAExecutionProvider"


class PaddleOcrGpuError(RuntimeError):
    """Raised when PaddleOCR GPU cannot be initialized or CUDA is not used."""


class OfficialPaddleGpuOcr:
    """PaddleOCR 3.x over onnxruntime-gpu. CUDA is mandatory."""

    def __init__(
        self,
        *,
        language: str = "pt",
        ocr_version: str = "PP-OCRv5",
        rec_batch_size: int = 16,
        device_id: int = 0,
        det_limit_side_len: int = 1920,
    ) -> None:
        if not prepare_paddle_gpu_runtime():
            raise PaddleOcrGpuError(
                "PaddleOCR GPU falhou: onnxruntime-gpu não expõe CUDAExecutionProvider. "
                "Instale onnxruntime-gpu>=1.27 e confirme o driver NVIDIA. "
                "Este modo não cai para CPU."
            )
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise PaddleOcrGpuError(
                "PaddleOCR GPU falhou: o pacote 'paddleocr' não está instalado. "
                "Rode: pip install 'paddleocr>=3.7,<3.8'. Este modo não cai para CPU."
            ) from exc

        engine_config = {
            "providers": [_CUDA_PROVIDER],
            "provider_options": [{"device_id": int(device_id)}],
        }
        try:
            self._ocr = PaddleOCR(
                lang=language,
                ocr_version=ocr_version,
                device=f"gpu:{int(device_id)}",
                engine="onnxruntime",
                engine_config=engine_config,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_recognition_batch_size=int(rec_batch_size),
                text_det_limit_side_len=int(det_limit_side_len),
            )
        except Exception as exc:
            raise PaddleOcrGpuError(
                "PaddleOCR GPU falhou ao criar o pipeline CUDA "
                f"(device=gpu:{device_id}, engine=onnxruntime): {exc}. "
                "Este modo não cai para CPU."
            ) from exc

        providers = self._require_cuda_sessions()
        logger.info(
            "PaddleOCR oficial na GPU: lang=%s version=%s device=gpu:%s "
            "rec_batch=%s det_limit=%s providers=%s",
            language,
            ocr_version,
            device_id,
            rec_batch_size,
            det_limit_side_len,
            providers,
        )
        self._lock = threading.Lock()

    def ocr_png_bytes(self, png_bytes: bytes) -> Tuple[str, bool, Optional[str], List[str]]:
        image = _decode_png_bgr(png_bytes)
        return self.ocr_image(image)

    def ocr_image(self, image: np.ndarray) -> Tuple[str, bool, Optional[str], List[str]]:
        with self._lock:
            results = list(self._ocr.predict(image))
        texts: List[str] = []
        for item in results:
            texts.extend(_rec_texts(item))
        body = "\n".join(t.strip() for t in texts if t and str(t).strip()).strip()
        return body, False, None, []

    def _require_cuda_sessions(self) -> List[str]:
        sessions = list(_iter_ort_sessions(self._ocr))
        if not sessions:
            raise PaddleOcrGpuError(
                "PaddleOCR GPU falhou: nenhuma sessão ONNX Runtime foi criada. "
                "Este modo não cai para CPU."
            )
        used: List[str] = []
        for session in sessions:
            providers = [str(p) for p in session.get_providers()]
            if not session_uses_cuda(session):
                raise PaddleOcrGpuError(
                    "PaddleOCR GPU falhou: a sessão ONNX Runtime não está em CUDA "
                    f"(providers={providers}). Este modo não cai para CPU."
                )
            used.append(",".join(providers))
        return used


def _decode_png_bgr(png_bytes: bytes) -> np.ndarray:
    import cv2

    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise PaddleOcrGpuError("PaddleOCR GPU: não foi possível decodificar a imagem PNG.")
    return image


def _rec_texts(result: Any) -> List[str]:
    texts = None
    if isinstance(result, dict) or hasattr(result, "get"):
        try:
            texts = result.get("rec_texts")  # type: ignore[union-attr]
        except Exception:
            texts = None
    if texts is None:
        texts = getattr(result, "rec_texts", None)
    if texts is None:
        return []
    if isinstance(texts, str):
        return [texts]
    return [str(t) for t in texts if t is not None]


def _iter_ort_sessions(root: Any) -> Iterable[Any]:
    seen: set[int] = set()
    queue: deque[Any] = deque([root])
    skip_types = (str, bytes, bytearray, memoryview, np.ndarray)
    while queue and len(seen) < 4000:
        obj = queue.popleft()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        if obj is None or isinstance(obj, skip_types):
            continue
        if _looks_like_ort_session(obj):
            yield obj
            continue
        if isinstance(obj, dict):
            queue.extend(obj.values())
            continue
        if isinstance(obj, (list, tuple, set)):
            queue.extend(obj)
            continue
        data = getattr(obj, "__dict__", None)
        if isinstance(data, dict) and data:
            queue.extend(data.values())


def _looks_like_ort_session(obj: Any) -> bool:
    get_providers = getattr(obj, "get_providers", None)
    run = getattr(obj, "run", None)
    if not callable(get_providers) or not callable(run):
        return False
    owner = getattr(get_providers, "__self__", None)
    return owner is obj
