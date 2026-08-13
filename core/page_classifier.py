"""
Page classifier for PJe PDFs.

Strips electronic stamps (Fls., assinatura, Unico overlay, validation URLs)
so residual native text can be measured, then combines that with pdfimages
geometry to label pages as native / hybrid / image_page.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
# Letterhead / office banner: wide but short
LETTERHEAD_MIN_WIDTH = 1200
LETTERHEAD_MAX_HEIGHT = 400
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
        return (
            self.width >= LETTERHEAD_MIN_WIDTH
            and self.height <= LETTERHEAD_MAX_HEIGHT
            and self.width > self.height * 2.5
        )

    def is_full_page_scan(self) -> bool:
        return self.width >= A4_SCAN_MIN_WIDTH and self.height >= A4_SCAN_MIN_HEIGHT

    def is_content_figure(self) -> bool:
        if self.is_logo_or_icon() or self.is_letterhead_strip():
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

_FLS_RE = re.compile(r"(?mi)^\s*Fls\.?\s*:\s*(\d+)\s*$")
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
    if shutil.which("pdfinfo") is None:
        return None
    try:
        output = subprocess.check_output(
            ["pdfinfo", str(pdf_path)],
            stderr=subprocess.DEVNULL,
            text=True,
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

    fls_match = _FLS_RE.search(sanitized)
    if fls_match:
        try:
            stamp.fls = int(fls_match.group(1))
        except ValueError:
            stamp.fls = None

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
    return shutil.which("pdfimages") is not None


def list_pdf_images(pdf_path: str | Path) -> List[PageImageInfo]:
    """Parse `pdfimages -list` into PageImageInfo rows (skips soft masks)."""
    pdf_path = Path(pdf_path)
    if not pdfimages_available():
        logger.warning("pdfimages not available; image coverage will be zero")
        return []

    try:
        output = subprocess.check_output(
            ["pdfimages", "-list", str(pdf_path)],
            stderr=subprocess.DEVNULL,
            text=True,
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


def extract_page_images_to_dir(
    pdf_path: str | Path,
    page_num: int,
    output_dir: Path,
    prefix: str = "page",
    *,
    skip_logos: bool = True,
) -> List[Path]:
    """
    Extract embedded images for one physical page into output_dir.

    When skip_logos=True, drops tiny decorative icons.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not pdfimages_available():
        return []

    root = output_dir / f"{prefix}_{page_num:04d}"
    try:
        subprocess.check_call(
            [
                "pdfimages",
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
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.warning(
            "pdfimages extract failed for page %s: %s", page_num, exc
        )
        return []

    saved = sorted(output_dir.glob(f"{prefix}_{page_num:04d}*"))
    if not skip_logos:
        return [p for p in saved if p.is_file()]

    # Drop tiny decorative icons (< 2KB) and very small dimensions via filesize
    meaningful = [p for p in saved if p.is_file() and p.stat().st_size >= 2048]
    return meaningful if meaningful else [p for p in saved if p.is_file()]


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
