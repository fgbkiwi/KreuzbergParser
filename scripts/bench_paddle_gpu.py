"""
Benchmark PaddleOCR-on-ONNXRuntime-CUDA detection settings against real PJe pages.

Renders pages of a PDF once, then times `predict` per detection-resize policy
so speed and recovered text can be compared on identical input.

    python scripts/bench_paddle_gpu.py <pdf> --pages 16 17 18 --sweep max/1600 min/1920
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.ort_runtime import prepare_paddle_gpu_runtime  # noqa: E402

DEFAULT_SWEEP = ["max/1280", "max/1600", "max/1920", "max/2400", "min/1920"]


def parse_spec(spec: str) -> Tuple[str, int]:
    limit_type, _, side = spec.partition("/")
    if limit_type not in {"max", "min"} or not side.isdigit():
        raise argparse.ArgumentTypeError(f"spec inválido: {spec!r} (use max/1600)")
    return limit_type, int(side)


def render_pages(pdf: Path, pages: Sequence[int], dpi: int) -> List[Any]:
    import cv2
    import kreuzberg
    import numpy as np

    images = []
    for page in pages:
        png = kreuzberg.render_pdf_page(str(pdf), page - 1, dpi=dpi)
        arr = np.frombuffer(png, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        print(f"  page {page}: {image.shape[1]}x{image.shape[0]}")
        images.append(image)
    return images


def build_ocr(
    limit_type: str,
    side_len: int,
    device_id: int,
    rec_batch: int,
    algo: str,
):
    from paddleocr import PaddleOCR

    provider_options: Dict[str, Any] = {"device_id": device_id}
    if algo:
        provider_options["cudnn_conv_algo_search"] = algo

    return PaddleOCR(
        lang="pt",
        ocr_version="PP-OCRv5",
        device=f"gpu:{device_id}",
        engine="onnxruntime",
        engine_config={
            "providers": ["CUDAExecutionProvider"],
            "provider_options": [provider_options],
        },
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_recognition_batch_size=rec_batch,
        text_det_limit_side_len=side_len,
        text_det_limit_type=limit_type,
    )


def rec_texts(results) -> List[str]:
    out: List[str] = []
    for item in results:
        texts = None
        try:
            texts = item.get("rec_texts")
        except Exception:
            texts = getattr(item, "rec_texts", None)
        out.extend(str(t) for t in (texts or []))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", type=int, nargs="+", default=[16, 17, 18])
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--rec-batch", type=int, default=16)
    parser.add_argument("--algo", default="HEURISTIC", help="cudnn_conv_algo_search")
    parser.add_argument("--sweep", nargs="+", default=DEFAULT_SWEEP)
    parser.add_argument(
        "--dump-dir",
        type=Path,
        help="grava o texto de cada variante para comparação manual",
    )
    args = parser.parse_args()

    if not prepare_paddle_gpu_runtime():
        print("CUDAExecutionProvider indisponível", file=sys.stderr)
        return 1

    specs = [parse_spec(s) for s in args.sweep]
    print(f"Rendering {len(args.pages)} pages at {args.dpi} DPI")
    images = render_pages(args.pdf, args.pages, args.dpi)
    if args.dump_dir:
        args.dump_dir.mkdir(parents=True, exist_ok=True)

    for limit_type, side_len in specs:
        print(f"\n=== limit={limit_type}/{side_len} algo={args.algo or 'default'} ===")
        ocr = build_ocr(
            limit_type, side_len, args.device_id, args.rec_batch, args.algo
        )
        times: List[float] = []
        for i, image in enumerate(images):
            t0 = time.time()
            results = list(ocr.predict(image))
            elapsed = time.time() - t0
            times.append(elapsed)
            texts = rec_texts(results)
            chars = sum(len(t) for t in texts)
            print(
                f"  page {args.pages[i]}: {elapsed:6.2f}s  "
                f"linhas={len(texts):4d}  chars={chars:5d}"
            )
            if args.dump_dir:
                out = args.dump_dir / (
                    f"p{args.pages[i]:04d}_{limit_type}{side_len}.txt"
                )
                out.write_text("\n".join(texts), encoding="utf-8")
        print(f"  total={sum(times):.2f}s  média={sum(times) / len(times):.2f}s")
        del ocr

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
