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


def _rapidocr_params(*, rec_batch_num: int, model_dir: Path) -> dict:
    """RapidOCR constructor params for Portuguese PJe scans.

    The default Chinese 0/180 angle classifier (``Global.use_cls``) flips
    roughly every other line of upright Latin legal text. Recognition then
    yields garbage below ``text_score`` 0.5 and RapidOCR drops the line
    with no warning — e.g. the procuraçao clause starting ``5584/70,
    confessar, desistir...``.
    """
    from rapidocr.utils.typings import LangDet, LangRec, ModelType, OCRVersion

    return {
        "EngineConfig.onnxruntime.use_cuda": True,
        "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
        "Rec.lang_type": LangRec.LATIN,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "Rec.rec_batch_num": max(1, int(rec_batch_num)),
        "Det.lang_type": LangDet.EN,
        # PJe rasters are already upright. The CH classifier is not.
        "Global.use_cls": False,
        # "error": empty detection on photo pages is expected and would
        # otherwise spam "The text detection result is empty" per figure.
        "Global.log_level": "error",
        "Global.model_root_dir": str(model_dir),
    }


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

        model_dir = _model_root_dir()
        params = _rapidocr_params(rec_batch_num=rec_batch_num, model_dir=model_dir)
        logger.info(
            "Starting RapidOCR GPU (PP-OCRv5 latin + onnxruntime-gpu CUDA, "
            "use_cls=false, models=%s)",
            model_dir,
        )
        _engine = RapidOCR(params=params)
        _engine_rec_batch = rec_batch_num
        # Belt-and-suspenders: RapidOCR uses its own logger module.
        try:
            from rapidocr.utils.log import logger as rapid_logger

            rapid_logger.setLevel(logging.ERROR)
        except Exception:
            pass
        return _engine


def _boxes_to_text(boxes, txts: Sequence) -> str:
    """Join OCR snippets in visual reading order (top-to-bottom, left-to-right)."""
    entries: List[Tuple[float, float, float, str]] = []
    for i, raw in enumerate(txts or ()):
        text = str(raw).strip() if raw else ""
        if not text:
            continue
        y0 = float(i)
        x0 = 0.0
        height = 10.0
        if boxes is not None and i < len(boxes):
            box = np.asarray(boxes[i], dtype=float)
            if box.ndim == 2 and box.shape[-1] >= 2 and box.size >= 4:
                xs = box[:, 0]
                ys = box[:, 1]
                x0 = float(xs.min())
                y0 = float(ys.min())
                height = max(float(ys.max() - y0), 1.0)
        entries.append((y0, x0, height, text))
    if not entries:
        return ""

    med_h = float(np.median([h for _y, _x, h, _t in entries])) or 10.0
    line_tol = max(10.0, 0.5 * med_h)
    entries.sort(key=lambda e: (e[0], e[1]))
    lines: List[List[Tuple[float, float, float, str]]] = []
    current: List[Tuple[float, float, float, str]] = [entries[0]]
    current_y = entries[0][0]
    for entry in entries[1:]:
        if abs(entry[0] - current_y) <= line_tol:
            current.append(entry)
        else:
            current.sort(key=lambda e: e[1])
            lines.append(current)
            current = [entry]
            current_y = entry[0]
    current.sort(key=lambda e: e[1])
    lines.append(current)
    return "\n".join(" ".join(item[3] for item in line) for line in lines).strip()


def _output_to_tuple(out) -> Tuple[str, bool, Optional[str], List[str]]:
    txts = getattr(out, "txts", None) or ()
    boxes = getattr(out, "boxes", None)
    text = _boxes_to_text(boxes, txts)
    return text, False, "pt", []


def ocr_png_bytes(
    png_bytes: bytes,
    *,
    rec_batch_num: int = 8,
) -> Tuple[str, bool, Optional[str], List[str]]:
    engine = get_rapid_ocr_engine(rec_batch_num=rec_batch_num)
    image = _png_to_rgb(png_bytes)
    # Per-call override in case a cached engine still has use_cls=True.
    out = engine(image, use_cls=False)
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
        results.append(_output_to_tuple(engine(image, use_cls=False)))
    return results
