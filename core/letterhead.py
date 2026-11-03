"""
Strip repeating petition letterheads and contact footers from native text.

Only touches header/footer bands. Body addresses and party names are kept.
Intimação, despacho, decisão and sentença are left intact: those pages still
vote in the repeated-line sets, but the cut never applies to them, so a
judge's closing survives no matter how many intimações quote the same act.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from core.page_classifier import PageClassification

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
# Require DDD in parentheses, +55, or a 2-digit area code as its own token.
# The old optional-parens pattern matched bank accounts (60008841-5) and
# unpunctuated PIX/CPF keys (05311008121) and deleted them from footers.
_PHONE_RE = re.compile(
    r"(?:\+55\s*)?(?:\(\s*\d{2}\s*\)|\b\d{2}\b)\s*(?:9\s*)?\d{4,5}[-\s]?\d{4}\b"
)
_BULLET_RE = re.compile(r"^[\s●•·‣▪\-–—*]+")
_CEP_RE = re.compile(r"\b\d{5}-\d{3}\b")
_OAB_RE = re.compile(r"\bOAB(?:/[A-Z]{2})?\s*(?:n[ºo°.]?\s*)?\d", re.I)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_FIRM_RE = re.compile(
    r"(?i)\b(?:sociedade\s+de\s+advogados|advogados\s+associados|"
    r"escrit[oó]rio\s+de\s+advocacia|advocacia\s+\w+)\b"
)
_ADDR_RE = re.compile(
    r"(?i)^\s*(?:rua|r\.|av(?:enida)?\.?|al(?:ameda)?\.?|pra[cç]a|pc\.|"
    r"travessa|tv\.|rod(?:ovia)?\.?)\b"
)
_NAME_PARTICLE_RE = re.compile(r"(?i)^(d[aeo]s?|e)$")

_KEEP_RE = re.compile(
    r"(?i)\b(?:excelent[ií]ssim|merit[ií]ssim|juiz|ju[ií]za|vara|"
    r"reclamante|reclamad[ao]|autor[ae]?|r[eé]u|processo|"
    r"n[uú]cleo|f[oó]rum|tribunal|agravo|recurso|embargos|"
    r"fls\.?|p[aá]gina)\b"
)

_HEADER_LINES = 8
_FOOTER_LINES = 8
_MAX_BANNER_CHARS = 70


def _norm_line(line: str) -> str:
    text = re.sub(r"\s+", " ", (line or "").strip().lower())
    return text


def _non_empty_lines(text: str) -> List[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _looks_like_person_name(line: str) -> bool:
    """True for a short signature-style name, not a firm banner."""
    stripped = (line or "").strip()
    if not stripped or _OAB_RE.search(stripped) or _FIRM_RE.search(stripped):
        return False
    if re.search(r"(?i)advogad", stripped):
        return False
    if any(ch.isdigit() for ch in stripped):
        return False
    if _EMAIL_RE.search(stripped) or _URL_RE.search(stripped) or _PHONE_RE.search(stripped):
        return False
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ'.]+", stripped) if w]
    core = [w for w in words if not _NAME_PARTICLE_RE.match(w)]
    if not (2 <= len(core) <= 6):
        return False
    caps = sum(1 for w in core if w[:1].isupper())
    return caps >= max(2, len(core) - 1)


def _looks_like_contact(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if _KEEP_RE.search(stripped):
        return False
    # Body prose of a procuraçao / petição often cites OAB, CEP or an
    # address. Those lines sit in the footer band of short pages and must
    # not be treated as letterhead just because they contain a contact token.
    # Count on collapsed whitespace: pdftotext pads contact rows with spaces.
    norm = _norm_line(stripped)
    if len(norm) > 90 or len(norm.split()) > 12:
        return False
    return bool(
        _EMAIL_RE.search(stripped)
        or _PHONE_RE.search(stripped)
        or _CEP_RE.search(stripped)
        or _OAB_RE.search(stripped)
        or _URL_RE.search(stripped)
        or _FIRM_RE.search(stripped)
        or _ADDR_RE.search(stripped)
    )


def _is_banner_line(line: str) -> bool:
    """Short repeating chrome. Petition openings and signatures stay out."""
    stripped = (line or "").strip()
    norm = _norm_line(stripped)
    if len(norm) < 8 or len(norm) > _MAX_BANNER_CHARS:
        return False
    if _KEEP_RE.search(stripped):
        return False
    if _looks_like_person_name(stripped):
        return False
    if len(norm.split()) > 10:
        return False
    # Wrapped paragraph lines and petition bullets are body, not a banner.
    lead = _BULLET_RE.sub("", stripped).lstrip()
    if lead[:1].islower():
        return False
    if re.match(r"(?i)^(?:CPF|CNPJ|PIX|Banco|Ag[eê]ncia|Conta|Chave)\b", lead):
        return False
    return True


def _header_footer_slices(text: str) -> tuple[List[str], List[str], List[str]]:
    lines = _non_empty_lines(text)
    if not lines:
        return [], [], []
    n = len(lines)
    if n <= _HEADER_LINES + _FOOTER_LINES:
        mid = max(1, n // 3)
        return (
            lines[:mid],
            lines[mid:-mid] if n > 2 * mid else [],
            lines[-mid:],
        )
    return lines[:_HEADER_LINES], lines[_HEADER_LINES:-_FOOTER_LINES], lines[-_FOOTER_LINES:]


def _band_cutoffs(lines: Sequence[str]) -> tuple[int, int]:
    """Last header index and first footer index in the raw (sparse) line list.

    Short pages use the same 1/3 split as ``_header_footer_slices``. Using
    a fixed 8+8 here used to mark *every* line as header and footer when the
    page had fewer than 16 nonempty lines, so an OAB in the closing vanished.
    """
    nonempty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    n = len(nonempty_idx)
    if n == 0:
        return -1, 0
    if n <= _HEADER_LINES + _FOOTER_LINES:
        mid = max(1, n // 3)
        return nonempty_idx[mid - 1], nonempty_idx[n - mid]
    return nonempty_idx[_HEADER_LINES - 1], nonempty_idx[n - _FOOTER_LINES]


def _repeated_band_lines(
    pages: Sequence[PageClassification],
    *,
    min_pages: int,
    band: str,
) -> Set[str]:
    counter: Counter[str] = Counter()
    eligible = 0
    for page in pages:
        if page.page_class not in {"native", "hybrid"}:
            continue
        if page.force_image_ocr:
            continue
        header, _body, footer = _header_footer_slices(page.residual_text or "")
        band_lines = header if band == "header" else footer
        norms = {
            _norm_line(ln)
            for ln in band_lines
            if _is_banner_line(ln)
        }
        if not norms:
            continue
        eligible += 1
        counter.update(norms)
    if eligible < min_pages:
        return set()
    return {line for line, count in counter.items() if count >= min_pages}


def _drop_band_lines(
    text: str,
    *,
    repeated_header: Iterable[str],
    repeated_footer: Iterable[str],
) -> str:
    header_set = set(repeated_header)
    footer_set = set(repeated_footer)
    lines = (text or "").splitlines()
    header_cutoff, footer_start = _band_cutoffs(lines)
    if header_cutoff < 0:
        return (text or "").strip()

    kept: List[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        norm = _norm_line(line)
        in_header = idx <= header_cutoff
        in_footer = idx >= footer_start
        drop_header = in_header and (
            (norm in header_set and _is_banner_line(line))
            or _looks_like_contact(line)
        )
        drop_footer = in_footer and (
            (norm in footer_set and _is_banner_line(line))
            or _looks_like_contact(line)
        )
        if drop_header or drop_footer:
            continue
        kept.append(line)

    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _preserved_judicial_page(page: PageClassification) -> bool:
    """True when this page must keep its closing and any quoted dispositivo."""
    if getattr(page, "letterhead_exempt", False):
        return True
    from core.pje_sumario import is_letterhead_preserved_tipo

    return is_letterhead_preserved_tipo(getattr(page, "sumario_tipo", None))


def apply_letterhead_strip(
    pages: Sequence[PageClassification],
    *,
    min_repeat_pages: int = 3,
) -> None:
    """Mutate residual_text on native/hybrid pages to drop banners/footers.

    Intimação, despacho, decisão and sentença are excluded from the cut.
    They still contribute lines to the repeated header/footer sets.
    """
    repeated_header = _repeated_band_lines(
        pages, min_pages=min_repeat_pages, band="header"
    )
    repeated_footer = _repeated_band_lines(
        pages, min_pages=min_repeat_pages, band="footer"
    )
    for page in pages:
        if page.page_class not in {"native", "hybrid"}:
            continue
        if page.force_image_ocr:
            continue
        if _preserved_judicial_page(page):
            continue
        page.residual_text = _drop_band_lines(
            page.residual_text or "",
            repeated_header=repeated_header,
            repeated_footer=repeated_footer,
        )
        page.residual_chars = len(page.residual_text)
