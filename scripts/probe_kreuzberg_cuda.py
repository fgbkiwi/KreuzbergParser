"""Probe: Kreuzberg nativo PaddleOCR + AccelerationConfig(cuda).

Roda o mesmo bootstrap do app (utils.ort_runtime) e extrai uma imagem
sintética com backend paddleocr e provider cuda. RUST_LOG mostra qual
execution provider o ONNX Runtime nativo realmente usou.

Uso: .venv/bin/python scripts/probe_kreuzberg_cuda.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
os.environ.setdefault("RUST_LOG", "kreuzberg=debug,ort=debug")

from utils.ort_runtime import prepare_paddle_gpu_runtime, ort_runtime_snapshot

ready = prepare_paddle_gpu_runtime()
print(f"[probe] prepare_paddle_gpu_runtime -> {ready}")
print(f"[probe] snapshot: {ort_runtime_snapshot()}")
if not ready:
    sys.exit("[probe] onnxruntime-gpu sem CUDAExecutionProvider — abortando")

import kreuzberg  # noqa: E402  (após ORT_DYLIB_PATH)

print(f"[probe] kreuzberg {kreuzberg.__version__}")

from io import BytesIO  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

img = Image.new("RGB", (640, 128), "white")
ImageDraw.Draw(img).text((10, 40), "Teste PaddleOCR GPU 12345", fill="black")
buf = BytesIO()
img.save(buf, format="PNG")

cfg = kreuzberg.ExtractionConfig(
    ocr=kreuzberg.OcrConfig(
        backend="paddleocr",
        language="pt",
        paddle_ocr_config=kreuzberg.PaddleOcrConfig(
            language="pt", model_tier="server", padding=16, rec_batch_num=16
        ),
    ),
    force_ocr=True,
    acceleration=kreuzberg.AccelerationConfig(provider="cuda", device_id=0),
)

t0 = time.perf_counter()
res = kreuzberg.extract_bytes_sync(buf.getvalue(), "image/png", config=cfg)
dt = time.perf_counter() - t0
print(f"[probe] OK em {dt:.2f}s — conteúdo: {res.content!r}")
