"""
Page geometry from Poppler pdftohtml -xml.

Used to place hybrid figures in reading order and to flag letterhead /
footer banners by position on the page.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.poppler import poppler_tool, subprocess_kwargs

logger = logging.getLogger(__name__)

_PAGE_OPEN_RE = re.compile(
    r"<page\b([^>]*)>",
    re.IGNORECASE,
)
_ATTR_RE = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:]*)\s*=\s*"([^"]*)"')
_IMAGE_RE = re.compile(r"<image\b([^>]*)/?>", re.IGNORECASE)
_TEXT_RE = re.compile(
    r"<text\b([^>]*)>(.*?)</text>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

_layout_cache: Dict[str, Dict[int, "PageLayout"]] = {}


@dataclass
class LayoutRect:
    x: float
    y: float
    width: float
    height: float
    text: str = ""

    @property
    def y_bottom(self) -> float:
        return self.y + self.height


@dataclass
class PageLayout:
    page: int
    width: float
    height: float
    text_blocks: List[LayoutRect] = field(default_factory=list)
    images: List[LayoutRect] = field(default_factory=list)


def _attrs(blob: str) -> Dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(blob or "")}


def _float_attr(attrs: Dict[str, str], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        raw = attrs.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return default


def _decode_xml_text(raw: str) -> str:
    text = _TAG_RE.sub("", raw or "")
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&#160;", " ")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def _parse_pdftohtml_xml(xml_text: str) -> Dict[int, PageLayout]:
    pages: Dict[int, PageLayout] = {}
    if not xml_text:
        return pages

    # Split on page tags so malformed inner XML does not abort the whole file.
    parts = re.split(r"(?i)(?=<page\b)", xml_text)
    for part in parts:
        open_match = _PAGE_OPEN_RE.search(part)
        if not open_match:
            continue
        attrs = _attrs(open_match.group(1))
        page_num = int(_float_attr(attrs, "number", default=0))
        if page_num <= 0:
            continue
        width = _float_attr(attrs, "width", default=595.0)
        height = _float_attr(attrs, "height", default=842.0)
        layout = PageLayout(page=page_num, width=width, height=height)

        for img_match in _IMAGE_RE.finditer(part):
            iattrs = _attrs(img_match.group(1))
            layout.images.append(
                LayoutRect(
                    x=_float_attr(iattrs, "left", "x"),
                    y=_float_attr(iattrs, "top", "y"),
                    width=_float_attr(iattrs, "width"),
                    height=_float_attr(iattrs, "height"),
                )
            )

        for text_match in _TEXT_RE.finditer(part):
            tattrs = _attrs(text_match.group(1))
            content = _decode_xml_text(text_match.group(2))
            if not content:
                continue
            layout.text_blocks.append(
                LayoutRect(
                    x=_float_attr(tattrs, "left", "x"),
                    y=_float_attr(tattrs, "top", "y"),
                    width=_float_attr(tattrs, "width"),
                    height=_float_attr(tattrs, "height"),
                    text=content,
                )
            )

        pages[page_num] = layout
    return pages


def load_pdf_layouts(pdf_path: str | Path) -> Dict[int, PageLayout]:
    """Parse pdftohtml -xml for the whole PDF (cached per path)."""
    pdf_path = Path(pdf_path)
    cache_key = str(pdf_path.resolve())
    cached = _layout_cache.get(cache_key)
    if cached is not None:
        return cached

    pdftohtml = poppler_tool("pdftohtml")
    if pdftohtml is None:
        logger.debug("pdftohtml not available; hybrid layout merge disabled")
        _layout_cache[cache_key] = {}
        return {}

    try:
        with tempfile.TemporaryDirectory(prefix="kb_layout_") as tmp:
            stem = Path(tmp) / "layout"
            cmd = [
                pdftohtml,
                "-xml",
                "-q",
                "-nodrm",
                "-hidden",
                str(pdf_path),
                str(stem),
            ]
            subprocess.check_call(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **subprocess_kwargs(),
            )
            xml_file = Path(str(stem) + ".xml")
            if not xml_file.is_file():
                xml_file = Path(tmp) / "layout.xml"
            xml_text = xml_file.read_text(encoding="utf-8", errors="replace") if xml_file.is_file() else ""
    except (subprocess.CalledProcessError, OSError, UnicodeError) as exc:
        logger.warning("pdftohtml -xml failed for %s: %s", pdf_path.name, exc)
        _layout_cache[cache_key] = {}
        return {}

    pages = _parse_pdftohtml_xml(xml_text)
    _layout_cache[cache_key] = pages
    logger.debug("Loaded layout for %s pages from %s", len(pages), pdf_path.name)
    return pages


def attach_image_positions(
    images: Sequence,
    layouts: Dict[int, PageLayout],
) -> None:
    """
    Pair pdfimages -list rows (in stream order) with xml <image> rects on
    the same page. Mutates PageImageInfo in place.
    """
    by_page: Dict[int, list] = {}
    for img in images:
        by_page.setdefault(img.page, []).append(img)

    for page, page_imgs in by_page.items():
        layout = layouts.get(page)
        if layout is None or not layout.images:
            continue
        ordered = sorted(page_imgs, key=lambda i: i.num)
        count = min(len(ordered), len(layout.images))
        for idx in range(count):
            info = ordered[idx]
            rect = layout.images[idx]
            info.x = rect.x
            info.y = rect.y
            info.display_width = rect.width
            info.display_height = rect.height
            info.page_width = layout.width
            info.page_height = layout.height


def group_text_paragraphs(
    blocks: Sequence[LayoutRect],
    y_gap: float = 14.0,
) -> List[LayoutRect]:
    """Merge xml text lines into coarser reading-order paragraphs."""
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b.y, b.x))
    paragraphs: List[LayoutRect] = []
    current: Optional[LayoutRect] = None
    for block in ordered:
        if current is None:
            current = LayoutRect(
                x=block.x,
                y=block.y,
                width=block.width,
                height=block.height,
                text=block.text,
            )
            continue
        gap = block.y - current.y_bottom
        if gap <= y_gap:
            current.text = f"{current.text}\n{block.text}".strip()
            current.width = max(current.width, block.x + block.width - current.x)
            current.height = max(current.height, block.y_bottom - current.y)
        else:
            paragraphs.append(current)
            current = LayoutRect(
                x=block.x,
                y=block.y,
                width=block.width,
                height=block.height,
                text=block.text,
            )
    if current is not None:
        paragraphs.append(current)
    return paragraphs


def native_text_covers_rect(
    layout: Optional[PageLayout],
    rect: LayoutRect,
    *,
    min_chars: int = 40,
) -> bool:
    """True when enough native xml text already sits inside the figure rect."""
    if layout is None or rect.width <= 0 or rect.height <= 0:
        return False
    chars = 0
    for block in layout.text_blocks:
        cx = block.x + block.width / 2
        cy = block.y + block.height / 2
        if rect.x <= cx <= rect.x + rect.width and rect.y <= cy <= rect.y + rect.height:
            chars += len(block.text)
    return chars >= min_chars


def is_duplicate_figure_text(ocr_text: str, native_text: str) -> bool:
    """True when figure OCR largely repeats text already in the native layer."""
    from difflib import SequenceMatcher

    ocr_n = re.sub(r"\s+", " ", (ocr_text or "").strip().lower())
    native_n = re.sub(r"\s+", " ", (native_text or "").strip().lower())
    if len(ocr_n) < 24 or not native_n:
        return False
    if ocr_n in native_n:
        return True
    if SequenceMatcher(None, ocr_n, native_n).ratio() >= 0.72:
        return True
    ocr_words = [w for w in re.findall(r"\w+", ocr_n) if len(w) > 2]
    if not ocr_words:
        return False
    native_words = set(re.findall(r"\w+", native_n))
    overlap = sum(1 for w in ocr_words if w in native_words)
    return (overlap / len(ocr_words)) >= 0.82


def merge_reading_order(
    paragraphs: Sequence[LayoutRect],
    figures: Sequence[Tuple[float, float, str]],
) -> str:
    """
    Interleave native paragraphs and figure OCR by Y (then X).

    figures: list of (y, x, text)
    """
    items: List[Tuple[float, float, str]] = []
    for para in paragraphs:
        if para.text.strip():
            items.append((para.y, para.x, para.text.strip()))
    for y, x, text in figures:
        if (text or "").strip():
            items.append((y, x, text.strip()))
    items.sort(key=lambda t: (t[0], t[1]))
    parts = [text for _y, _x, text in items if text]
    merged = "\n\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", merged).strip()
