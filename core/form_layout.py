"""Geometric layout helpers for labor-form reconstruction.

Runs Tesseract ``image_to_data`` to obtain word bounding boxes, groups them
into visual lines and column bands, and detects table grids via OpenCV.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image

    _TESS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional at import time
    pytesseract = None
    Image = None  # type: ignore
    _TESS_AVAILABLE = False

try:
    import cv2
    import numpy as np

    _CV2_AVAILABLE = True
except ImportError:  # pragma: no cover
    cv2 = None
    np = None  # type: ignore
    _CV2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float = -1.0

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


@dataclass
class Line:
    words: List[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words if w.text).strip()

    @property
    def x(self) -> int:
        return min(w.x for w in self.words) if self.words else 0

    @property
    def y(self) -> int:
        return min(w.y for w in self.words) if self.words else 0

    @property
    def x2(self) -> int:
        return max(w.x2 for w in self.words) if self.words else 0

    @property
    def y2(self) -> int:
        return max(w.y2 for w in self.words) if self.words else 0

    @property
    def h(self) -> int:
        return max(1, self.y2 - self.y)

    @property
    def cy(self) -> float:
        return (self.y + self.y2) / 2.0


@dataclass
class Cell:
    x: int
    y: int
    w: int
    h: int
    text: str = ""
    colspan: int = 1
    rowspan: int = 1
    row: int = 0
    col: int = 0

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h


@dataclass
class TableGrid:
    cells: List[List[Cell]] = field(default_factory=list)
    xs: List[int] = field(default_factory=list)
    ys: List[int] = field(default_factory=list)

    @property
    def n_rows(self) -> int:
        return len(self.cells)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.cells), default=0)


# ---------------------------------------------------------------------------
# OCR text normalization
# ---------------------------------------------------------------------------

_OCR_FIXES: Sequence[Tuple[re.Pattern[str], str]] = (
    (re.compile(r"\bFis\.:", re.I), "Fls.:"),
    (re.compile(r"\b16\s+CER\b"), "16 CEP"),
    (re.compile(r"\bCER\b"), "CEP"),
    (re.compile(r"\b130\s+[Ss]al[aá]rio"), "13o Salário"),
    (re.compile(r"\b64\.1\s+130\s+[Ss]al[aá]rio"), "64.1 13o Salário"),
    (re.compile(r"\b70\s+130\s+[Ss]al[aá]rio"), "70 13o Salário"),
    (re.compile(r"8\s+8[ºo°]"), "§ 8º"),
    (re.compile(r"81[ºo°]"), "§ 1º"),
    (re.compile(r"CIC:"), "C/C:"),
    (re.compile(r"nydus\s+ru\b", re.I), "nydus RH"),
    (re.compile(r"\$J(\d)"), r"SJ\1"),
    (re.compile(r"\b0\.00\b"), "0,00"),
    # 4.70540 → 4.705,40 (missing decimal comma)
    (re.compile(r"\b(\d{1,3})\.(\d{3})(\d{2})\b"), r"\1.\2,\3"),
)


def normalize_ocr_text(text: str) -> str:
    """Apply common Tesseract misreads found in Brazilian labor forms."""
    out = text or ""
    for pattern, repl in _OCR_FIXES:
        out = pattern.sub(repl, out)
    return out


def normalize_word_text(text: str) -> str:
    return normalize_ocr_text((text or "").strip())


# ---------------------------------------------------------------------------
# Tesseract word extraction
# ---------------------------------------------------------------------------


def tesseract_available() -> bool:
    """True when a Tesseract binary can be invoked by pytesseract."""
    if not _TESS_AVAILABLE:
        return False
    return _ensure_tesseract_cmd()


_tesseract_resolved = False


def _tesseract_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("tesseract.exe", "tesseract")
    return ("tesseract", "tesseract.exe")


def _tesseract_in_dir(directory: Path) -> Path | None:
    for name in _tesseract_names():
        candidate = directory / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _common_tesseract_dirs() -> Iterable[Path]:
    home = Path.home()
    yield home / "scoop" / "apps" / "tesseract" / "current"
    if sys.platform == "win32":
        yield Path(r"C:\Program Files\Tesseract-OCR")
        yield Path(r"C:\Program Files (x86)\Tesseract-OCR")
        yield Path(sys.prefix) / "Library" / "bin"
    else:
        yield Path("/usr/bin")
        yield Path("/usr/local/bin")
        yield Path("/opt/homebrew/bin")


def _find_tesseract_binary() -> Path | None:
    """Locate a Tesseract executable (PATH, env vars, common install dirs)."""
    which = shutil.which("tesseract")
    if which:
        path = Path(which)
        if path.is_file():
            return path.resolve()

    for key in ("TESSERACT_CMD", "TESSERACT_PATH"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_file():
            return path.resolve()
        if path.is_dir():
            found = _tesseract_in_dir(path)
            if found is not None:
                return found

    for directory in _common_tesseract_dirs():
        found = _tesseract_in_dir(directory)
        if found is not None:
            return found
    return None


def _ensure_tesseract_cmd() -> bool:
    global _tesseract_resolved
    if not _TESS_AVAILABLE:
        return False

    if _tesseract_resolved:
        current = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
        return bool(current and Path(str(current)).is_file())

    current = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    if current and current != "tesseract":
        path = Path(str(current))
        if path.is_file():
            _tesseract_resolved = True
            return True

    found = _find_tesseract_binary()
    if found is None:
        return False

    pytesseract.pytesseract.tesseract_cmd = str(found)
    _tesseract_resolved = True
    logger.info("Tesseract cmd=%s", found)
    return True


def ocr_words(
    png_bytes: bytes,
    *,
    lang: str = "por+eng",
    psms: Sequence[int] = (6, 11),
    min_conf: float = 0.0,
) -> List[Word]:
    """Return OCR words with bounding boxes, merging PSM 6 and PSM 11."""
    if not _TESS_AVAILABLE:
        logger.warning("pytesseract/Pillow not available; cannot extract word boxes")
        return []
    if not _ensure_tesseract_cmd():
        logger.debug("Tesseract binary not found; skipping image_to_data")
        return []
    if not png_bytes:
        return []

    try:
        image = Image.open(io.BytesIO(png_bytes))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
    except Exception as exc:
        logger.warning("Failed to open raster for TSV OCR: %s", exc)
        return []

    merged: List[Word] = []
    for psm in psms:
        words = _image_to_words(image, lang=lang, psm=psm, min_conf=min_conf)
        if not merged:
            merged = words
            continue
        merged = _merge_word_lists(merged, words)

    normalized: List[Word] = []
    for word in merged:
        text = normalize_word_text(word.text)
        if not text:
            continue
        normalized.append(
            Word(text=text, x=word.x, y=word.y, w=word.w, h=word.h, conf=word.conf)
        )
    return normalized


def _image_to_words(
    image,
    *,
    lang: str,
    psm: int,
    min_conf: float,
) -> List[Word]:
    try:
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        logger.debug("Tesseract PSM %s failed: %s", psm, exc)
        return []

    n = len(data.get("text") or [])
    words: List[Word] = []
    for i in range(n):
        raw = (data["text"][i] or "").strip()
        if not raw:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf >= 0 and conf < min_conf:
            continue
        try:
            x, y, w, h = (
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["width"][i]),
                int(data["height"][i]),
            )
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        words.append(Word(text=raw, x=x, y=y, w=w, h=h, conf=conf))
    return words


def _iou(a: Word, b: Word) -> float:
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if not inter:
        return 0.0
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union else 0.0


def _merge_word_lists(primary: List[Word], extra: List[Word], iou_thresh: float = 0.35) -> List[Word]:
    out = list(primary)
    for word in extra:
        if any(_iou(word, existing) >= iou_thresh for existing in out):
            # Keep higher-confidence duplicate
            for i, existing in enumerate(out):
                if _iou(word, existing) >= iou_thresh and word.conf > existing.conf:
                    out[i] = word
                    break
            continue
        out.append(word)
    out.sort(key=lambda w: (w.y, w.x))
    return out


# ---------------------------------------------------------------------------
# Line / column grouping
# ---------------------------------------------------------------------------


def group_lines(words: Sequence[Word], y_tol_ratio: float = 0.55) -> List[Line]:
    """Cluster words into visual reading-order lines."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (w.y, w.x))
    lines: List[Line] = []
    current = Line(words=[ordered[0]])
    for word in ordered[1:]:
        ref_h = max(current.h, word.h, 1)
        if abs(word.cy - current.cy) <= ref_h * y_tol_ratio:
            current.words.append(word)
        else:
            current.words.sort(key=lambda w: w.x)
            lines.append(current)
            current = Line(words=[word])
    current.words.sort(key=lambda w: w.x)
    lines.append(current)
    return lines


def cluster_column_bands(
    words: Sequence[Word],
    *,
    max_bands: int = 6,
    min_gap_ratio: float = 0.08,
) -> List[Tuple[int, int]]:
    """Return ``(x_start, x_end)`` bands for multi-column forms."""
    if not words:
        return []
    page_w = max(w.x2 for w in words)
    if page_w <= 0:
        return []
    xs = sorted(w.cx for w in words)
    gaps: List[Tuple[float, float, float]] = []
    for a, b in zip(xs, xs[1:]):
        gap = b - a
        if gap >= page_w * min_gap_ratio:
            gaps.append((gap, a, b))
    gaps.sort(reverse=True)
    cuts = sorted(mid for _, a, b in gaps[: max(0, max_bands - 1)] for mid in [(a + b) / 2])
    bounds = [0.0, *cuts, float(page_w)]
    bands: List[Tuple[int, int]] = []
    for i in range(len(bounds) - 1):
        bands.append((int(bounds[i]), int(bounds[i + 1])))
    return bands or [(0, page_w)]


def words_in_bbox(
    words: Sequence[Word],
    bbox: Tuple[int, int, int, int],
    *,
    min_overlap: float = 0.45,
) -> List[Word]:
    x, y, w, h = bbox
    x2, y2 = x + w, y + h
    found: List[Word] = []
    for word in words:
        ix1, iy1 = max(word.x, x), max(word.y, y)
        ix2, iy2 = min(word.x2, x2), min(word.y2, y2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area = max(1, word.w * word.h)
        if inter / area >= min_overlap or (x <= word.cx <= x2 and y <= word.cy <= y2):
            found.append(word)
    found.sort(key=lambda ww: (ww.y, ww.x))
    return found


def line_text_in_x_range(line: Line, x1: int, x2: int, margin: int = 8) -> str:
    parts = [w.text for w in line.words if x1 - margin <= w.cx <= x2 + margin]
    return " ".join(parts).strip()


def next_lines(lines: Sequence[Line], index: int, count: int = 3) -> List[Line]:
    return list(lines[index + 1 : index + 1 + count])


# ---------------------------------------------------------------------------
# OpenCV table grid
# ---------------------------------------------------------------------------


def detect_table_grid(
    png_bytes: bytes,
    words: Optional[Sequence[Word]] = None,
    *,
    min_rows: int = 2,
    min_cols: int = 2,
) -> Optional[TableGrid]:
    """Detect a ruled table via morphological line extraction."""
    if not _CV2_AVAILABLE or not png_bytes:
        return None
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h, w = binary.shape[:2]
        horiz_len = max(20, w // 30)
        vert_len = max(20, h // 40)
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1))
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len))
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel, iterations=1)
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel, iterations=1)
        xs = _line_positions(vertical, axis=0, min_length=h * 0.08)
        ys = _line_positions(horizontal, axis=1, min_length=w * 0.12)
        xs = _cluster_positions(xs, gap=max(8, w // 80))
        ys = _cluster_positions(ys, gap=max(8, h // 90))
        if len(xs) < min_cols + 1 or len(ys) < min_rows + 1:
            return None

        grid = TableGrid(xs=xs, ys=ys, cells=[])
        word_list = list(words or [])
        for r in range(len(ys) - 1):
            row_cells: List[Cell] = []
            c = 0
            while c < len(xs) - 1:
                colspan = 1
                # Merge empty-divider spans: if almost no ink on the inner vertical
                # line between c and c+1, still keep atomic cells; colspan is
                # inferred later from text overflow. Here we keep one cell per slot.
                cell = Cell(
                    x=xs[c],
                    y=ys[r],
                    w=xs[c + 1] - xs[c],
                    h=ys[r + 1] - ys[r],
                    row=r,
                    col=c,
                    colspan=colspan,
                )
                if word_list:
                    cell.text = " ".join(
                        ww.text
                        for ww in words_in_bbox(word_list, (cell.x, cell.y, cell.w, cell.h))
                    ).strip()
                row_cells.append(cell)
                c += 1
            grid.cells.append(_collapse_empty_leading(row_cells))
        if grid.n_rows < min_rows or grid.n_cols < min_cols:
            return None
        _apply_colspans(grid)
        return grid
    except Exception as exc:
        logger.debug("OpenCV grid detection failed: %s", exc)
        return None


def _line_positions(mask, axis: int, min_length: float) -> List[int]:
    """axis=0 → x positions of vertical lines; axis=1 → y of horizontal lines."""
    projection = mask.sum(axis=axis)
    thresh = max(int(min_length), 1) * 255 * 0.15
    hits = [i for i, val in enumerate(projection) if val > thresh]
    return hits


def _cluster_positions(positions: Sequence[int], gap: int) -> List[int]:
    if not positions:
        return []
    ordered = sorted(positions)
    clusters: List[List[int]] = [[ordered[0]]]
    for pos in ordered[1:]:
        if pos - clusters[-1][-1] <= gap:
            clusters[-1].append(pos)
        else:
            clusters.append([pos])
    return [int(sum(c) / len(c)) for c in clusters]


def _collapse_empty_leading(cells: List[Cell]) -> List[Cell]:
    return cells


def _apply_colspans(grid: TableGrid) -> None:
    """Mark colspan when a cell's text clearly continues into empty neighbors."""
    for row in grid.cells:
        i = 0
        while i < len(row):
            if row[i].text and i + 1 < len(row) and not row[i + 1].text:
                span = 1
                j = i + 1
                while j < len(row) and not row[j].text:
                    span += 1
                    j += 1
                if span > 1:
                    row[i].colspan = span
                    row[i].w = row[i + span - 1].x2 - row[i].x
            i += 1


def grid_to_html(
    grid: TableGrid,
    *,
    header_rows: int = 1,
    caption: Optional[str] = None,
) -> str:
    """Render a detected grid as an HTML table (LlamaParse-style)."""
    if not grid.cells:
        return ""
    lines = ["<table>"]
    if caption:
        lines.append(f"  <caption>{_escape_html(caption)}</caption>")
    body_started = False
    header_started = False
    for r, row in enumerate(grid.cells):
        is_header = r < header_rows
        if is_header and not header_started:
            lines.append("  <thead>")
            header_started = True
        if not is_header and header_started and not body_started:
            lines.append("  </thead>")
            lines.append("  <tbody>")
            body_started = True
        if not is_header and not body_started and not header_started:
            lines.append("  <tbody>")
            body_started = True
        lines.append("    <tr>")
        tag = "th" if is_header else "td"
        skip = 0
        for cell in row:
            if skip:
                skip -= 1
                continue
            attrs = ""
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
                skip = cell.colspan - 1
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            lines.append(f"        <{tag}{attrs}>{_escape_html(cell.text)}</{tag}>")
        lines.append("    </tr>")
    if header_started and not body_started:
        lines.append("  </thead>")
    if body_started:
        lines.append("  </tbody>")
    lines.append("</table>")
    return "\n".join(lines)


def rows_to_html(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    header_colspans: Optional[Sequence[int]] = None,
    caption: Optional[str] = None,
) -> str:
    """Build an HTML table from explicit headers/rows."""
    lines = ["<table>"]
    if caption:
        lines.append(f"  <caption>{_escape_html(caption)}</caption>")
    lines.append("  <thead>")
    lines.append("    <tr>")
    spans = list(header_colspans or [1] * len(headers))
    for header, span in zip(headers, spans):
        attr = f' colspan="{span}"' if span > 1 else ""
        lines.append(f"        <th{attr}>{_escape_html(header)}</th>")
    lines.append("    </tr>")
    lines.append("  </thead>")
    lines.append("  <tbody>")
    for row in rows:
        lines.append("    <tr>")
        for cell in row:
            if isinstance(cell, tuple) and len(cell) == 2:
                text, span = cell
                attr = f' colspan="{span}"' if span > 1 else ""
                lines.append(f"        <td{attr}>{_escape_html(str(text))}</td>")
            else:
                lines.append(f"        <td>{_escape_html(str(cell))}</td>")
        lines.append("    </tr>")
    lines.append("  </tbody>")
    lines.append("</table>")
    return "\n".join(lines)


def _escape_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def words_to_plain_text(words: Sequence[Word]) -> str:
    return "\n".join(line.text for line in group_lines(words) if line.text)


def iter_line_windows(lines: Sequence[Line]) -> Iterable[Tuple[int, Line, List[Line]]]:
    for i, line in enumerate(lines):
        yield i, line, list(lines[i + 1 : i + 4])
