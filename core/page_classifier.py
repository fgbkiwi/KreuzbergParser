"""
Page classifier for PJe PDFs.

Strips electronic stamps (Fls., assinatura, Unico overlay, validation URLs)
so residual native text can be measured, then combines that with pdfimages
geometry to label pages as native / hybrid / image_page.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from utils.poppler import poppler_tool, subprocess_kwargs

logger = logging.getLogger(__name__)

# CNJ process number: NNNNNNN-DD.AAAA.J.TR.OOOO
CNJ_PROCESS_RE = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")

# A4 at 72 DPI (PDF user space points ≈ pixels at 72dpi)
A4_WIDTH_PX = 595.0
A4_HEIGHT_PX = 842.0
A4_AREA = A4_WIDTH_PX * A4_HEIGHT_PX

# Heuristic image sizes (pixels of embedded image, not coverage/A4_72)
PRINT_MIN_ASPECT = 1.6  # height/width for tall screenshots
PRINT_MIN_HEIGHT = 900
GOVBR_STRIP_MAX_HEIGHT = 220
GOVBR_STRIP_MIN_WIDTH = 600
GOVBR_MIN_STRIPS = 3

# Full-page scan: large width AND height (real raster), not letterhead strips
A4_SCAN_MIN_WIDTH = 1500
A4_SCAN_MIN_HEIGHT = 2000
# Letterhead / office banner: wide but short (intrinsic pixels)
LETTERHEAD_MIN_WIDTH = 600
LETTERHEAD_MAX_HEIGHT = 400
LETTERHEAD_TOP_RATIO = 0.18
FOOTER_BOTTOM_RATIO = 0.15
BANNER_MAX_HEIGHT_RATIO = 0.22
# Tiny PJe icon / QR-ish logos
LOGO_MAX_SIDE = 120
LOGO_MAX_AREA = 25_000
# Meaningful content figure (screenshots, petition frames)
MEDIUM_FIGURE_MIN_AREA = 40_000
MEDIUM_FIGURE_MIN_HEIGHT = 200


@dataclass
class PageImageInfo:
    page: int
    num: int
    img_type: str
    width: int
    height: int
    encoding: str
    object_id: str = ""
    x: Optional[float] = None
    y: Optional[float] = None
    display_width: Optional[float] = None
    display_height: Optional[float] = None
    page_width: Optional[float] = None
    page_height: Optional[float] = None

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def coverage(self) -> float:
        if A4_AREA <= 0:
            return 0.0
        return self.area / A4_AREA

    @property
    def aspect_hw(self) -> float:
        if self.width <= 0:
            return 0.0
        return self.height / float(self.width)

    def is_logo_or_icon(self) -> bool:
        if max(self.width, self.height) <= LOGO_MAX_SIDE and self.area <= LOGO_MAX_AREA:
            return True
        # Soft square stamps ~87x87
        if self.width <= 150 and self.height <= 150:
            return True
        return False

    def is_letterhead_strip(self) -> bool:
        # Conservative intrinsic check (pdfimages pixels, no page position).
        return (
            self.width >= 1200
            and self.height <= LETTERHEAD_MAX_HEIGHT
            and self.width > self.height * 2.5
        )

    def _height_ratio(self) -> float:
        h = self.display_height if self.display_height else float(self.height)
        page_h = self.page_height or A4_HEIGHT_PX
        if page_h <= 0:
            return 0.0
        return h / page_h

    def _width_ratio(self) -> float:
        w = self.display_width if self.display_width else float(self.width)
        page_w = self.page_width or A4_WIDTH_PX
        if page_w <= 0:
            return 0.0
        return w / page_w

    def is_govbr_strip(self) -> bool:
        return (
            self.width >= GOVBR_STRIP_MIN_WIDTH
            and self.height <= GOVBR_STRIP_MAX_HEIGHT
            and self.width > self.height * 2
        )

    def is_header_banner(self) -> bool:
        if self.is_govbr_strip():
            return False
        if self.y is not None and self.page_height:
            top = self.y / self.page_height
            wide = self._width_ratio() >= 0.45 or (
                (self.display_width or self.width)
                > (self.display_height or self.height or 1) * 2.0
            )
            return (
                top <= LETTERHEAD_TOP_RATIO
                and self._height_ratio() <= BANNER_MAX_HEIGHT_RATIO
                and wide
            )
        return self.is_letterhead_strip()

    def is_footer_banner(self) -> bool:
        if self.is_govbr_strip():
            return False
        if self.y is None or not self.page_height:
            return False
        h = self.display_height if self.display_height else float(self.height)
        bottom = (self.y + h) / self.page_height
        return bottom >= (1.0 - FOOTER_BOTTOM_RATIO) and self._height_ratio() <= 0.18

    def is_banner_or_footer_image(self) -> bool:
        return self.is_header_banner() or self.is_footer_banner()

    def is_full_page_scan(self) -> bool:
        return self.width >= A4_SCAN_MIN_WIDTH and self.height >= A4_SCAN_MIN_HEIGHT

    def is_content_figure(self) -> bool:
        if self.is_logo_or_icon():
            return False
        if self.is_govbr_strip():
            return True
        if self.is_banner_or_footer_image():
            return False
        if self.is_full_page_scan():
            return True
        if self.area >= MEDIUM_FIGURE_MIN_AREA and self.height >= MEDIUM_FIGURE_MIN_HEIGHT:
            return True
        # Tall phone screenshots
        if self.height >= PRINT_MIN_HEIGHT and self.aspect_hw >= PRINT_MIN_ASPECT:
            return True
        # Gov.br horizontal strips are content for ID cards
        if (
            self.width >= GOVBR_STRIP_MIN_WIDTH
            and self.height <= GOVBR_STRIP_MAX_HEIGHT
            and self.width > self.height * 2
        ):
            return True
        return False


@dataclass
class PJeStampMeta:
    fls: Optional[int] = None
    signature: Optional[str] = None
    signature_ids: List[str] = field(default_factory=list)
    validation_url: Optional[str] = None
    document_number: Optional[str] = None
    process_number: Optional[str] = None
    raw_stamp: str = ""


@dataclass
class PageClassification:
    page: int
    page_class: str  # native | hybrid | image_page
    subtype: str
    residual_chars: int
    raw_chars: int
    max_image_coverage: float
    image_count: int
    strip_count: int
    stamp: PJeStampMeta = field(default_factory=PJeStampMeta)
    residual_text: str = ""
    images: List[PageImageInfo] = field(default_factory=list)
    content_images: List[PageImageInfo] = field(default_factory=list)
    force_image_ocr: bool = False
    doc_kind: Optional[str] = None
    sumario_tipo: Optional[str] = None
    sumario_titulo: Optional[str] = None


# ---------------------------------------------------------------------------
# PJe / Unico boilerplate stripping
# ---------------------------------------------------------------------------

# One or more folio numbers: "Fls.: 107", "Fls.: 2 107", "Fls.: 10Fls.: 115"
_FLS_RE = re.compile(r"(?mi)^\s*Fls\.?\s*:\s*(\d+)(?:\s+(\d+))?\s*$")
_FLS_ANY_RE = re.compile(r"(?i)F[il1]s\.?\s*:\s*(\d+)")


def expand_stacked_fls(numbers: Sequence[int]) -> List[int]:
    """
    Split PJe dual overlays concatenated by pdftotext (2 + 107 → 2107).

    Document folio is 1–2 digits; process folio is the remainder (3+ digits).
    """
    expanded: List[int] = []
    for raw in numbers:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        text = str(value)
        split = False
        if 4 <= len(text) <= 6:
            for left_len in (1, 2):
                if len(text) - left_len < 3:
                    continue
                left = int(text[:left_len])
                right = int(text[left_len:])
                if 1 <= left <= 40 and 50 <= right <= 9999 and right > left:
                    expanded.extend([left, right])
                    split = True
                    break
        if not split:
            expanded.append(value)
    return expanded


def collect_fls_numbers(text: str) -> List[int]:
    """All Fls. numbers on a page, including stacked dual overlays."""
    sanitized = (text or "").replace("\u00a0", " ")
    found: List[int] = []
    for match in _FLS_ANY_RE.finditer(sanitized):
        try:
            found.append(int(match.group(1)))
        except ValueError:
            continue
    for match in _FLS_RE.finditer(sanitized):
        if match.group(2):
            try:
                found.append(int(match.group(2)))
            except ValueError:
                pass
    return expand_stacked_fls(found)


def prefer_process_folio(numbers: Sequence[int]) -> Optional[int]:
    """Prefer the process folio when a document folio is also present."""
    values = [int(n) for n in numbers if n]
    if not values:
        return None
    return max(values)
_SIGNATURE_RE = re.compile(
    r"(?mi)^\s*Documento assinado eletronicamente por .+?(?:\n|$)",
)
_DIGITALLY_SIGNED_RE = re.compile(
    r"(?mi)^\s*Digitally signed by .+?(?:\n|$)",
)
_UNICO_SIGN_RE = re.compile(
    r"(?mi)^\s*unico\s*\|\s*sign.*?(?:\n|$)",
)
_UNICO_DATE_RE = re.compile(r"(?mi)^\s*Date:\s*.+$")
_UNICO_REASON_RE = re.compile(r"(?mi)^\s*Reason:\s*.+$")
_UNICO_LOCATION_RE = re.compile(r"(?mi)^\s*Location:\s*.*$")
_UNICO_CODIGO_RE = re.compile(
    r"(?mi)^\s*(?:unico\s*\|\s*sign\s*-?\s*)?C[oó]digo do documento\s*:.*$"
)
_UNICO_PAGINA_RE = re.compile(r"(?mi)^\s*P[aá]gina\s+\d+\s*$")
_PJE_URL_RE = re.compile(
    r"(?mi)^\s*https?://[^\s]*pje[^\s]*\.jus\.br\S*\s*$",
)
_DOC_NUMBER_RE = re.compile(
    r"(?mi)^\s*N[uú]mero do documento\s*:\s*\S+\s*$",
)
_PROC_NUMBER_LINE_RE = re.compile(
    r"(?mi)^\s*N[uú]mero do processo\s*:\s*\S+\s*$",
)
_PJE_FOOTER_BITS_RE = re.compile(
    r"(?mi)^\s*(?:PJe\s*-\s*\d|PROCESSO JUDICIAL ELETR[OÔ]NICO).*$",
)
_SERPRO_LINE_RE = re.compile(
    r"(?mi)^\s*(?:OBSERVA[CÇ][OÕ]ES|Assinador Serpro|Medida Provis[oó]ria).*$"
)


def extract_cnj_process_number(
    *sources: Optional[str],
    pdf_path: Optional[Path] = None,
) -> Optional[str]:
    """Extract CNJ process number from filename, metadata, or free text."""
    candidates: List[str] = []
    for source in sources:
        if source:
            candidates.append(str(source))

    if pdf_path is not None:
        candidates.append(Path(pdf_path).stem)
        candidates.append(Path(pdf_path).name)
        meta_title = _pdf_info_field(pdf_path, "Title")
        if meta_title:
            candidates.append(meta_title)
        meta_subject = _pdf_info_field(pdf_path, "Subject")
        if meta_subject:
            candidates.append(meta_subject)

    for text in candidates:
        match = CNJ_PROCESS_RE.search(text)
        if match:
            return match.group(0)
    return None


def _pdf_info_field(pdf_path: Path, field: str) -> Optional[str]:
    pdfinfo = poppler_tool("pdfinfo")
    if pdfinfo is None:
        return None
    try:
        output = subprocess.check_output(
            [pdfinfo, str(pdf_path)],
            stderr=subprocess.DEVNULL,
            text=True,
            **subprocess_kwargs(),
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    prefix = f"{field}:"
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return None


def extract_pje_stamp(text: str) -> PJeStampMeta:
    """Parse Fls. / signature / validation metadata from page text."""
    from core.pje_sumario import extract_signature_ids

    sanitized = (text or "").replace("\u00a0", " ")
    stamp = PJeStampMeta()

    stamp.fls = prefer_process_folio(collect_fls_numbers(sanitized))

    sig_match = _SIGNATURE_RE.search(sanitized)
    if sig_match:
        stamp.signature = sig_match.group(0).strip()

    stamp.signature_ids = extract_signature_ids(sanitized)

    url_match = re.search(r"(?i)https?://[^\s]*pje[^\s]*\.jus\.br\S*", sanitized)
    if url_match:
        stamp.validation_url = url_match.group(0).rstrip(".,;)")

    doc_match = re.search(
        r"(?mi)N[uú]mero do documento\s*:\s*(\S+)",
        sanitized,
    )
    if doc_match:
        stamp.document_number = doc_match.group(1).strip()

    proc_match = re.search(
        r"(?mi)N[uú]mero do processo\s*:\s*(\S+)",
        sanitized,
    )
    if proc_match:
        stamp.process_number = proc_match.group(1).strip()
    else:
        cnj = CNJ_PROCESS_RE.search(sanitized)
        if cnj:
            stamp.process_number = cnj.group(0)

    stamp_parts = []
    if stamp.fls is not None:
        stamp_parts.append(f"Fls.: {stamp.fls}")
    if stamp.signature:
        stamp_parts.append(stamp.signature)
    if stamp.validation_url:
        stamp_parts.append(stamp.validation_url)
    if stamp.document_number:
        stamp_parts.append(f"Número do documento: {stamp.document_number}")
    if stamp.process_number:
        stamp_parts.append(f"Número do processo: {stamp.process_number}")
    stamp.raw_stamp = "\n".join(stamp_parts)
    return stamp


def strip_pje_boilerplate(text: str) -> Tuple[str, PJeStampMeta]:
    """Remove PJe + Unico stamp lines; return residual text and stamp metadata."""
    stamp = extract_pje_stamp(text or "")
    sanitized = (text or "").replace("\u00a0", " ")

    cleaned = _FLS_RE.sub("", sanitized)
    cleaned = _FLS_ANY_RE.sub("", cleaned)
    cleaned = _SIGNATURE_RE.sub("", cleaned)
    cleaned = _DIGITALLY_SIGNED_RE.sub("", cleaned)
    cleaned = _UNICO_SIGN_RE.sub("", cleaned)
    cleaned = _UNICO_DATE_RE.sub("", cleaned)
    cleaned = _UNICO_REASON_RE.sub("", cleaned)
    cleaned = _UNICO_LOCATION_RE.sub("", cleaned)
    cleaned = _UNICO_CODIGO_RE.sub("", cleaned)
    cleaned = _UNICO_PAGINA_RE.sub("", cleaned)
    cleaned = _PJE_URL_RE.sub("", cleaned)
    cleaned = _DOC_NUMBER_RE.sub("", cleaned)
    cleaned = _PROC_NUMBER_LINE_RE.sub("", cleaned)
    cleaned = _PJE_FOOTER_BITS_RE.sub("", cleaned)
    cleaned = _SERPRO_LINE_RE.sub("", cleaned)
    # Inline validation URLs
    cleaned = re.sub(r"(?i)https?://[^\s]*pje[^\s]*\.jus\.br\S*", "", cleaned)
    # Office letterhead URL often alone on a line
    cleaned = re.sub(
        r"(?mi)^\s*www\.[^\s]+\s*(?:AM\s*\|.*)?\s*$",
        "",
        cleaned,
    )

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, stamp


def residual_is_overlay_only(residual_text: str, min_chars: int = 200) -> bool:
    """True when leftover text is too short / only certificate fluff."""
    text = (residual_text or "").strip()
    if len(text) < min_chars:
        return True
    # Mostly date/reason remnants that escaped strip
    low = text.lower()
    fluff = ("date:", "reason:", "location:", "este material foi criado", "página ")
    non_fluff = [
        ln for ln in text.splitlines()
        if ln.strip() and not any(f in ln.lower() for f in fluff)
    ]
    joined = "\n".join(non_fluff).strip()
    return len(joined) < min_chars


# ---------------------------------------------------------------------------
# pdfimages -list
# ---------------------------------------------------------------------------

def pdfimages_available() -> bool:
    return poppler_tool("pdfimages") is not None


def list_pdf_images(pdf_path: str | Path) -> List[PageImageInfo]:
    """Parse `pdfimages -list` into PageImageInfo rows (skips soft masks)."""
    pdf_path = Path(pdf_path)
    pdfimages = poppler_tool("pdfimages")
    if pdfimages is None:
        logger.warning("pdfimages not available; image coverage will be zero")
        return []

    try:
        output = subprocess.check_output(
            [pdfimages, "-list", str(pdf_path)],
            stderr=subprocess.DEVNULL,
            text=True,
            **subprocess_kwargs(),
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning("pdfimages -list failed for %s: %s", pdf_path.name, exc)
        return []

    images: List[PageImageInfo] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("page") or stripped.startswith("-"):
            continue
        parts = stripped.split()
        if len(parts) < 10:
            continue
        try:
            page = int(parts[0])
            num = int(parts[1])
            img_type = parts[2].lower()
            width = int(parts[3])
            height = int(parts[4])
            encoding = parts[8].lower() if len(parts) > 8 else ""
            object_id = parts[10] if len(parts) > 10 else ""
        except (ValueError, IndexError):
            continue

        if img_type in {"smask", "mask"}:
            continue

        images.append(
            PageImageInfo(
                page=page,
                num=num,
                img_type=img_type,
                width=width,
                height=height,
                encoding=encoding,
                object_id=object_id,
            )
        )
    return images


def _image_index_from_name(path: Path) -> Optional[int]:
    match = re.search(r"-(\d+)$", path.stem)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def extract_page_images_to_dir(
    pdf_path: str | Path,
    page_num: int,
    output_dir: Path,
    prefix: str = "page",
    *,
    skip_logos: bool = True,
    page_images: Optional[Sequence[PageImageInfo]] = None,
) -> List[Path]:
    """
    Extract embedded images for one physical page into output_dir.

    When skip_logos=True, drops tiny decorative icons and banner/footer images.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfimages = poppler_tool("pdfimages")
    if pdfimages is None:
        return []

    root = output_dir / f"{prefix}_{page_num:04d}"
    try:
        subprocess.check_call(
            [
                pdfimages,
                "-f",
                str(page_num),
                "-l",
                str(page_num),
                "-all",
                str(pdf_path),
                str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **subprocess_kwargs(),
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning(
            "pdfimages extract failed for page %s: %s", page_num, exc
        )
        return []

    saved = sorted(
        p for p in output_dir.glob(f"{prefix}_{page_num:04d}*") if p.is_file()
    )
    if not skip_logos:
        return saved

    by_num = {img.num: img for img in (page_images or [])}
    meaningful: List[Path] = []
    for path in saved:
        if path.stat().st_size < 2048:
            continue
        idx = _image_index_from_name(path)
        info = by_num.get(idx) if idx is not None else None
        if info is None and idx is not None:
            info = by_num.get(idx + 1)
        if info is not None and (
            info.is_logo_or_icon() or info.is_banner_or_footer_image()
        ):
            continue
        if info is not None and not info.is_content_figure():
            continue
        meaningful.append(path)
    return meaningful if meaningful else [p for p in saved if p.stat().st_size >= 2048]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _count_horizontal_strips(images: Sequence[PageImageInfo]) -> int:
    count = 0
    for img in images:
        if (
            img.width >= GOVBR_STRIP_MIN_WIDTH
            and img.height <= GOVBR_STRIP_MAX_HEIGHT
            and img.width > img.height * 2
        ):
            count += 1
    return count


def _infer_subtype(
    page_class: str,
    images: Sequence[PageImageInfo],
    content_images: Sequence[PageImageInfo],
    strip_count: int,
) -> str:
    if page_class == "hybrid":
        return "petition_hybrid"
    if page_class == "native":
        return "native_text"

    for img in content_images:
        if img.height >= PRINT_MIN_HEIGHT and img.aspect_hw >= PRINT_MIN_ASPECT:
            return "print_chat"

    if strip_count >= GOVBR_MIN_STRIPS:
        return "govbr_strip_id"

    if any(img.is_full_page_scan() for img in content_images):
        return "a4_scan"

    if not content_images:
        # Overlay-only text + logos, or text converted to vector outlines.
        return "vector_outline"

    if content_images or images:
        return "image_page"
    return "unknown"


def classify_page(
    page_num: int,
    raw_text: str,
    page_images: Sequence[PageImageInfo],
    *,
    residual_native_chars: int = 200,
    residual_hybrid_chars: int = 120,
    force_image_ocr: bool = False,
    doc_kind: Optional[str] = None,
    sumario_tipo: Optional[str] = None,
    sumario_titulo: Optional[str] = None,
    is_petition_like: bool = False,
) -> PageClassification:
    """Classify a single physical page."""
    residual_text, stamp = strip_pje_boilerplate(raw_text or "")
    residual_chars = len(residual_text)
    raw_chars = len((raw_text or "").strip())
    overlay_only = residual_is_overlay_only(residual_text, residual_native_chars)

    all_imgs = list(page_images)
    content_images = [img for img in all_imgs if img.is_content_figure()]
    strip_count = _count_horizontal_strips(all_imgs)
    has_full_page = any(img.is_full_page_scan() for img in content_images)
    has_content_figures = bool(content_images)
    max_coverage = max((img.coverage for img in content_images), default=0.0)

    # Forced image forms from SUMÁRIO (TRCT, CD/SD, ficha, etc.)
    if force_image_ocr:
        page_class = "image_page"
    elif overlay_only and (has_full_page or has_content_figures):
        page_class = "image_page"
    elif overlay_only and not has_content_figures:
        # No text, no figures — still try raster OCR (blank-ish or vector form)
        page_class = "image_page"
    elif not overlay_only and has_content_figures and not has_full_page:
        # Real native text + content figures (petition with frames)
        page_class = "hybrid"
    elif not overlay_only and has_content_figures and has_full_page and is_petition_like:
        # Petition-like with a large embedded figure: hybrid to avoid wiping text
        page_class = "hybrid"
    elif not overlay_only and residual_chars >= residual_native_chars and not has_content_figures:
        page_class = "native"
    elif not overlay_only and residual_chars >= residual_hybrid_chars and has_content_figures:
        page_class = "hybrid"
    elif residual_chars >= residual_native_chars and not has_full_page:
        page_class = "native"
    else:
        page_class = "image_page" if (has_content_figures or overlay_only) else "native"

    subtype = _infer_subtype(page_class, all_imgs, content_images, strip_count)

    return PageClassification(
        page=page_num,
        page_class=page_class,
        subtype=subtype,
        residual_chars=residual_chars,
        raw_chars=raw_chars,
        max_image_coverage=round(max_coverage, 4),
        image_count=len(content_images),
        strip_count=strip_count,
        stamp=stamp,
        residual_text=residual_text,
        images=list(all_imgs),
        content_images=list(content_images),
        force_image_ocr=force_image_ocr,
        doc_kind=doc_kind,
        sumario_tipo=sumario_tipo,
        sumario_titulo=sumario_titulo,
    )


def classify_pdf_pages(
    pdf_path: str | Path,
    page_texts: Sequence[str],
    *,
    residual_native_chars: int = 200,
    residual_hybrid_chars: int = 120,
    sumario: Optional[Dict] = None,
    full_page_coverage: float = 0.40,  # kept for API compat; geometry used instead
    medium_figure_coverage: float = 0.08,
) -> List[PageClassification]:
    """Classify every physical page given pdftotext output per page."""
    from core.pje_sumario import resolve_entry_for_page, tipo_to_kind

    _ = full_page_coverage, medium_figure_coverage  # legacy knobs unused

    pdf_path = Path(pdf_path)
    all_images = list_pdf_images(pdf_path)
    try:
        from core.page_layout import attach_image_positions, load_pdf_layouts

        attach_image_positions(all_images, load_pdf_layouts(pdf_path))
    except Exception as exc:
        logger.debug("Page layout attach skipped: %s", exc)

    by_page: Dict[int, List[PageImageInfo]] = {}
    for img in all_images:
        by_page.setdefault(img.page, []).append(img)

    sumario = sumario or {}
    results: List[PageClassification] = []
    for idx, text in enumerate(page_texts, start=1):
        stamp_preview = extract_pje_stamp(text or "")
        entry = resolve_entry_for_page(stamp_preview.signature_ids, sumario)
        force = bool(entry and entry.is_force_image_form())
        petition = bool(entry and entry.is_petition_like())
        kind = None
        tipo = None
        titulo = None
        if entry:
            tipo = entry.tipo
            titulo = entry.titulo
            kind = tipo_to_kind(entry.tipo, entry.titulo)
            # Documento Diverso / generic → don't force; leave kind None for OCR fallback
            if entry.is_generic() and not entry.is_force_image_form():
                force = False
                kind = None

        results.append(
            classify_page(
                idx,
                text,
                by_page.get(idx, []),
                residual_native_chars=residual_native_chars,
                residual_hybrid_chars=residual_hybrid_chars,
                force_image_ocr=force,
                doc_kind=kind,
                sumario_tipo=tipo,
                sumario_titulo=titulo,
                is_petition_like=petition,
            )
        )
    return results
