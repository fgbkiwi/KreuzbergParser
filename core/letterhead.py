"""
Strip repeating petition letterheads and contact footers from native text.

Only touches header/footer bands. Body addresses and party names are kept.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Sequence, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from core.page_classifier import PageClassification

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(
    r"(?:\+55\s*)?(?:\(?\d{2}\)?\s*)?(?:9\s*)?\d{4,5}[-\s]?\d{4}"
)
_CEP_RE = re.compile(r"\b\d{5}-?\d{3}\b")
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

_KEEP_RE = re.compile(
    r"(?i)\b(?:excelent[ií]ssim|merit[ií]ssim|juiz|ju[ií]za|vara|"
    r"reclamante|reclamad[ao]|autor[ae]?|r[eé]u|processo|"
    r"n[uú]cleo|f[oó]rum|tribunal|agravo|recurso|embargos|"
    r"fls\.?|p[aá]gina)\b"
)

_HEADER_LINES = 8
_FOOTER_LINES = 8


def _norm_line(line: str) -> str:
    text = re.sub(r"\s+", " ", (line or "").strip().lower())
    return text


def _non_empty_lines(text: str) -> List[str]:
    return [ln for ln in (text or "").splitlines() if ln.strip()]


def _looks_like_contact(line: str) -> bool:
    if not line.strip():
        return False
    if _KEEP_RE.search(line):
        return False
    return bool(
        _EMAIL_RE.search(line)
        or _PHONE_RE.search(line)
        or _CEP_RE.search(line)
        or _OAB_RE.search(line)
        or _URL_RE.search(line)
        or _FIRM_RE.search(line)
        or _ADDR_RE.search(line)
    )


def _header_footer_slices(text: str) -> tuple[List[str], List[str], List[str]]:
    lines = _non_empty_lines(text)
    if not lines:
        return [], [], []
    if len(lines) <= _HEADER_LINES + _FOOTER_LINES:
        mid = max(1, len(lines) // 3)
        return lines[:mid], lines[mid:-mid] if len(lines) > 2 * mid else [], lines[-mid:]
    return lines[:_HEADER_LINES], lines[_HEADER_LINES:-_FOOTER_LINES], lines[-_FOOTER_LINES:]


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
        norms = {_norm_line(ln) for ln in band_lines if len(_norm_line(ln)) >= 8}
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
    nonempty_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    if not nonempty_idx:
        return (text or "").strip()

    n_nonempty = len(nonempty_idx)
    header_cutoff = nonempty_idx[min(_HEADER_LINES, n_nonempty) - 1]
    footer_start = nonempty_idx[max(0, n_nonempty - _FOOTER_LINES)]

    kept: List[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        norm = _norm_line(line)
        in_header = idx <= header_cutoff
        in_footer = idx >= footer_start
        if in_header and (norm in header_set or _looks_like_contact(line)):
            continue
        if in_footer and (norm in footer_set or _looks_like_contact(line)):
            continue
        kept.append(line)

    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def apply_letterhead_strip(
    pages: Sequence[PageClassification],
    *,
    min_repeat_pages: int = 3,
) -> None:
    """Mutate residual_text on native/hybrid pages to drop banners/footers."""
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
        page.residual_text = _drop_band_lines(
            page.residual_text or "",
            repeated_header=repeated_header,
            repeated_footer=repeated_footer,
        )
        page.residual_chars = len(page.residual_text)
