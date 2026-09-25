"""
RapidOCR on CUDA via onnxruntime-gpu (PP-OCR ONNX).

Used by ProcessingMode.GPU as the Windows-friendly GPU OCR path
(replaces EasyOCR). Shares onnxruntime-gpu with the PaddleOCR GPU stack.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()
_engine_rec_batch: Optional[int] = None


def _png_to_rgb(png_bytes: bytes) -> np.ndarray:
    import cv2

    buf = np.frombuffer(png_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Could not decode PNG bytes for RapidOCR")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def get_rapid_ocr_engine(*, rec_batch_num: int = 8):
    """Lazy singleton RapidOCR engine with CUDA + Latin recognizer."""
    global _engine, _engine_rec_batch
    with _engine_lock:
        if _engine is not None and _engine_rec_batch == rec_batch_num:
            return _engine
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangDet, LangRec, ModelType, OCRVersion

        from utils.ort_runtime import prepare_paddle_gpu_runtime

        if not prepare_paddle_gpu_runtime():
            raise RuntimeError(
                "onnxruntime-gpu CUDAExecutionProvider is not available. "
                "Install onnxruntime-gpu>=1.27 (CUDA 13) — see GPU_SETUP.md."
            )

        params = {
            "EngineConfig.onnxruntime.use_cuda": True,
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
            "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.rec_batch_num": max(1, int(rec_batch_num)),
            "Det.lang_type": LangDet.EN,
            "Global.log_level": "warning",
        }
        logger.info(
            "Starting RapidOCR GPU (PP-OCRv5 latin + onnxruntime-gpu CUDA)"
        )
        _engine = RapidOCR(params=params)
        _engine_rec_batch = rec_batch_num
        return _engine


def _output_to_tuple(out) -> Tuple[str, bool, Optional[str], List[str]]:
    txts = getattr(out, "txts", None) or ()
    text = "\n".join(str(t) for t in txts if t).strip()
    return text, False, "pt", []


def ocr_png_bytes(
    png_bytes: bytes,
    *,
    rec_batch_num: int = 8,
) -> Tuple[str, bool, Optional[str], List[str]]:
    engine = get_rapid_ocr_engine(rec_batch_num=rec_batch_num)
    image = _png_to_rgb(png_bytes)
    out = engine(image)
    return _output_to_tuple(out)


def ocr_png_batch(
    png_list: Sequence[bytes],
    *,
    rec_batch_num: int = 8,
) -> List[Tuple[str, bool, Optional[str], List[str]]]:
    engine = get_rapid_ocr_engine(rec_batch_num=rec_batch_num)
    results = []
    for png_bytes in png_list:
        image = _png_to_rgb(png_bytes)
        results.append(_output_to_tuple(engine(image)))
    return results
