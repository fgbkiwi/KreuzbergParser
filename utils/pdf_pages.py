"""Physical PDF page text extraction via Poppler utilities."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def poppler_available() -> bool:
    """Return True when pdftotext and pdfinfo are available on PATH."""
    return shutil.which("pdftotext") is not None and shutil.which("pdfinfo") is not None


def get_page_count(pdf_path: str | Path) -> int:
    """Return the number of physical pages in a PDF."""
    pdf_path = Path(pdf_path)
    output = subprocess.check_output(
        ["pdfinfo", str(pdf_path)],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for line in output.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Could not determine page count for {pdf_path}")


def extract_pages_text(pdf_path: str | Path) -> List[str]:
    """
    Extract text for each physical PDF page.

    Uses a single pdftotext invocation and splits on form-feed page breaks.
    Falls back to one pdftotext call per page when form feeds are absent.
    """
    pdf_path = Path(pdf_path)
    if not poppler_available():
        raise RuntimeError("Poppler utilities (pdftotext/pdfinfo) are not available")

    expected_pages = get_page_count(pdf_path)
    raw_text = subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8", "replace")

    if "\f" in raw_text:
        parts = raw_text.split("\f")
        pages = [part.strip() for part in parts if part.strip()]
        if len(pages) == expected_pages:
            return pages
        if len(pages) == expected_pages + 1 and not parts[-1].strip():
            return [part.strip() for part in parts[:expected_pages]]
        logger.warning(
            "Form-feed split returned %s pages, expected %s; using per-page extraction",
            len(pages),
            expected_pages,
        )

    logger.info("Falling back to per-page pdftotext extraction for %s", pdf_path.name)
    return [_extract_single_page_text(pdf_path, page_num) for page_num in range(1, expected_pages + 1)]


def _extract_single_page_text(pdf_path: Path, page_num: int) -> str:
    text = subprocess.check_output(
        [
            "pdftotext",
            "-f",
            str(page_num),
            "-l",
            str(page_num),
            "-layout",
            str(pdf_path),
            "-",
        ],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8", "replace")
    return text.strip()
