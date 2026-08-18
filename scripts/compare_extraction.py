#!/usr/bin/env python3
"""Compare structured form extraction against a LlamaParse markdown gabarito.

Default corpus (38-page subset + LlamaParse reference):

    python scripts/compare_extraction.py

Only critical labor-form leaves (TRCT, ficha, recibo, FGTS):

    python scripts/compare_extraction.py --critical-only

Skip VLM (templates only, faster):

    python scripts/compare_extraction.py --no-vlm --critical-only
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PDF = (
    ROOT
    / "output"
    / "Processo_0000271-70.2026.5.10.0009-001 docs probelmáticos.pdf"
)
DEFAULT_REF = (
    ROOT
    / "output"
    / "Processo_0000271-70.2026.5.10.0009-001 docs probelmáticos llama-parse.md"
)

# Fls. markers in the 38-page subset, in physical page order.
SUBSET_FLS: List[int] = [
    50, 370, 372, 374, 398, 399, 400, 401, 402, 403,
    425, 426, 427, 428, 431, 432, 447, 448, 457, 458,
    459, 460, 461, 462, 463, 464, 466, 467, 468, 469,
    485, 486, 506, 559, 560, 563, 564, 566,
]

CRITICAL_FLS = (447, 448, 466, 467, 468, 469, 485, 486, 559, 560, 566)
MONEY_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
FLS_RE = re.compile(r"Fls\.:\s*(\d+)", re.I)
KV_RE = re.compile(r"\*\*(\d{1,3}(?:\.\d)?\s+[^:*]+)\*\*\s*:")
TABLE_RE = re.compile(r"<table[\s>]", re.I)

logger = logging.getLogger("compare_extraction")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REF)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--no-vlm", action="store_true")
    parser.add_argument("--critical-only", action="store_true")
    parser.add_argument(
        "--pages",
        type=str,
        default="",
        help="Physical page numbers (1-based), comma-separated",
    )
    parser.add_argument("--min-coverage", type=float, default=0.95)
    return parser.parse_args()


def fls_to_page(fls: int) -> Optional[int]:
    try:
        return SUBSET_FLS.index(fls) + 1
    except ValueError:
        return None


def page_to_fls(page: int) -> Optional[int]:
    if 1 <= page <= len(SUBSET_FLS):
        return SUBSET_FLS[page - 1]
    return None


def split_reference_by_fls(markdown: str) -> Dict[int, str]:
    hits = list(FLS_RE.finditer(markdown))
    sections: Dict[int, str] = {}
    for i, match in enumerate(hits):
        fls = int(match.group(1))
        start = match.start()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(markdown)
        sections[fls] = markdown[start:end]
    return sections


def money_values(text: str) -> List[str]:
    return MONEY_RE.findall(_strip_details(text or ""))


def _strip_details(text: str) -> str:
    return re.sub(
        r"<details\b[^>]*>.*?</details>",
        "",
        text or "",
        flags=re.I | re.S,
    )


def coverage(reference_vals: Sequence[str], extracted_vals: Sequence[str]) -> float:
    if not reference_vals:
        return 1.0
    ref_counts: Dict[str, int] = {}
    for val in reference_vals:
        ref_counts[val] = ref_counts.get(val, 0) + 1
    ext_counts: Dict[str, int] = {}
    for val in extracted_vals:
        ext_counts[val] = ext_counts.get(val, 0) + 1
    hit = 0
    total = 0
    for val, count in ref_counts.items():
        total += count
        hit += min(count, ext_counts.get(val, 0))
    return hit / total if total else 1.0


def render_page(pdf_path: Path, page: int, dpi: int) -> bytes:
    import kreuzberg

    return kreuzberg.render_pdf_page(str(pdf_path), page - 1, dpi=dpi)


def kreuzberg_ocr_text(png_bytes: bytes) -> str:
    """Plain OCR via Kreuzberg (embedded Tesseract) when system tesseract is absent."""
    try:
        import kreuzberg

        result = kreuzberg.extract_bytes_sync(png_bytes, "image/png")
        text = getattr(result, "content", None) or getattr(result, "text", None) or ""
        return str(text).strip()
    except Exception as exc:
        logger.debug("Kreuzberg OCR for harness failed: %s", exc)
        return ""


def extract_page(
    png_bytes: bytes,
    *,
    enable_vlm: bool,
    hint_kind: Optional[str] = None,
) -> Tuple[str, str, float, int]:
    from core.form_templates import try_structured_extraction

    ocr_text = kreuzberg_ocr_text(png_bytes)
    result = try_structured_extraction(
        png_bytes,
        kind=hint_kind,
        ocr_text=ocr_text,
        enable_vlm=enable_vlm,
    )
    if not result or not result.markdown.strip():
        return "", result.reason if result else "empty", 0.0, 0
    return result.markdown, result.source, result.fill_ratio, result.n_table_rows


def hint_kind_for_fls(fls: int) -> Optional[str]:
    if fls in (559, 560):
        return "trct"
    if fls in (466, 467, 468, 469):
        return "ficha_registro"
    if fls in (447, 448, 485, 486, 506):
        return "recibo"
    if fls == 566:
        return "fgts"
    return None


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.pdf.exists():
        logger.error("PDF não encontrado: %s", args.pdf)
        return 2
    if not args.reference.exists():
        logger.error("Gabarito não encontrado: %s", args.reference)
        return 2

    ref_sections = split_reference_by_fls(args.reference.read_text(encoding="utf-8"))
    if args.pages:
        pages = [int(p.strip()) for p in args.pages.split(",") if p.strip()]
    elif args.critical_only:
        pages = [fls_to_page(fls) for fls in CRITICAL_FLS]
        pages = [p for p in pages if p]
    else:
        pages = list(range(1, len(SUBSET_FLS) + 1))

    try:
        from config import Config
        from utils.tessdata import ensure_tessdata

        ensure_tessdata(Config.TESSDATA_DIR, Config.TESSERACT_LANGUAGES)
        from utils.poppler import ensure_poppler

        ensure_poppler(Config.POPPLER_DIR)
    except Exception as exc:
        logger.warning("Tessdata/Poppler setup skipped: %s", exc)

    logger.info(
        "Comparando %s página(s) de %s contra %s (VLM=%s)",
        len(pages),
        args.pdf.name,
        args.reference.name,
        not args.no_vlm,
    )

    rows = []
    critical_coverages: List[float] = []
    for page in pages:
        fls = page_to_fls(page)
        ref_text = ref_sections.get(fls or -1, "")
        ref_money = money_values(ref_text)
        try:
            png = render_page(args.pdf, page, args.dpi)
        except Exception as exc:
            logger.exception("Falha ao rasterizar página %s: %s", page, exc)
            rows.append((page, fls, "render_error", 0.0, 0, 0, 0, str(exc)))
            continue
        md, source, fill, n_tables = extract_page(
            png,
            enable_vlm=not args.no_vlm,
            hint_kind=hint_kind_for_fls(fls or 0),
        )
        ext_money = money_values(md)
        cov = coverage(ref_money, ext_money)
        ref_tables = len(TABLE_RE.findall(_strip_details(ref_text)))
        ext_tables = len(TABLE_RE.findall(_strip_details(md)))
        kv_ref = len(KV_RE.findall(_strip_details(ref_text)))
        kv_ext = len(KV_RE.findall(_strip_details(md)))
        rows.append(
            (page, fls, source or "none", cov, len(ref_money), len(ext_money),
             ref_tables, ext_tables, kv_ref, kv_ext, fill)
        )
        if fls in CRITICAL_FLS:
            critical_coverages.append(cov)
        logger.info(
            "p.%02d Fls.%s source=%s fill=%.2f money %s/%s (%.0f%%) tables ref=%s ext=%s kv %s/%s",
            page,
            fls,
            source or "none",
            fill,
            min(len(ref_money), len(set(ref_money) & set(ext_money))) if ref_money else 0,
            len(ref_money),
            cov * 100,
            ref_tables,
            ext_tables,
            kv_ext,
            kv_ref,
        )

    print()
    print(
        f"{'pág':>4} {'Fls':>5} {'fonte':<12} {'fill':>5} {'$cov':>6} "
        f"{'$ref':>5} {'$ext':>5} {'tblR':>4} {'tblE':>4} {'kvR':>4} {'kvE':>4}"
    )
    for row in rows:
        page, fls, source, cov, nref, next_, tref, text, kv_ref, kv_ext, fill = (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            row[8], row[9], row[10],
        )
        print(
            f"{page:4d} {str(fls or '-'):>5} {source:<12} {fill:5.2f} {cov:6.1%} "
            f"{nref:5d} {next_:5d} {tref:4d} {text:4d} {kv_ref:4d} {kv_ext:4d}"
        )

    if critical_coverages:
        mean = sum(critical_coverages) / len(critical_coverages)
        print()
        print(
            f"Cobertura monetária média (folhas críticas): {mean:.1%} "
            f"(meta {args.min_coverage:.0%}, n={len(critical_coverages)})"
        )
        if mean < args.min_coverage:
            print("META NÃO ATINGIDA — revise templates / ligue o VLM e compare de novo.")
            return 1
        print("Meta atingida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
