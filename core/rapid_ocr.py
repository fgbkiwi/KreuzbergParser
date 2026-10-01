"""
RapidOCR on CUDA via onnxruntime-gpu (PP-OCR ONNX).

Used by ProcessingMode.GPU as the Windows-friendly GPU OCR path
(replaces EasyOCR). Shares onnxruntime-gpu with the PaddleOCR GPU stack.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()
_engine_rec_batch: Optional[int] = None


def _model_root_dir() -> Path:
    """Writable RapidOCR model cache (never the Program Files package dir).

    RapidOCR defaults to ``<site-packages>/rapidocr/models``. In the Pynsist
    installer that path is read-only, so the first GPU run hangs or dies
    while downloading ONNX weights. Keep them next to tessdata/poppler.
    """
    override = (os.environ.get("RAPIDOCR_MODEL_DIR") or "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        try:
            from config import Config

            path = Path(Config.BASE_DIR) / "rapidocr_models"
        except Exception:
            home = (
                os.environ.get("KREUZBERG_PARSER_HOME")
                or os.environ.get("KIWI_DOWN_HOME")
                or ""
            ).strip()
            path = (
                Path(home) / "rapidocr_models"
                if home
                else Path.home() / "KiwiDown" / "rapidocr_models"
            )
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _png_to_rgb(png_bytes: bytes) -> np.ndarray:
    """Decode PNG bytes to RGB without ``cv2.imdecode``.

    OpenCV 5.x wheels have been observed to raise
    ``AttributeError: module 'cv2' has no attribute 'imdecode'`` under the
    GPU/RapidOCR path; Pillow is already a hard dependency and is reliable.
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png_bytes)) as img:
        rgb = img.convert("RGB")
        return np.asarray(rgb)


def get_rapid_ocr_engine(*, rec_batch_num: int = 8):
    """Lazy singleton RapidOCR engine with CUDA + Latin recognizer."""
    global _engine, _engine_rec_batch
    with _engine_lock:
        if _engine is not None and _engine_rec_batch == rec_batch_num:
            return _engine
        from utils.opencv_runtime import ensure_cv2
        from utils.ort_runtime import prepare_paddle_gpu_runtime

        if not prepare_paddle_gpu_runtime():
            raise RuntimeError(
                "onnxruntime-gpu CUDAExecutionProvider is not available. "
                "Install onnxruntime-gpu>=1.27 (CUDA 13) — see GPU_SETUP.md."
            )

        # RapidOCR's main.py does `import cv2` at module load.
        ensure_cv2()

        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangDet, LangRec, ModelType, OCRVersion

        model_dir = _model_root_dir()
        params = {
            "EngineConfig.onnxruntime.use_cuda": True,
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
            "Rec.lang_type": LangRec.LATIN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.rec_batch_num": max(1, int(rec_batch_num)),
            "Det.lang_type": LangDet.EN,
            "Global.log_level": "warning",
            "Global.model_root_dir": str(model_dir),
        }
        logger.info(
            "Starting RapidOCR GPU (PP-OCRv5 latin + onnxruntime-gpu CUDA, "
            "models=%s)",
            model_dir,
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
