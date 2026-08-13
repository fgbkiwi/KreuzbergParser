"""
Parse the PJe SUMÁRIO (document index) from PDF native text.

Maps the 7-character signature ID (suffix of
"Documento assinado eletronicamente por ... - XXXXXXX") to
{titulo, tipo} from the summary table at the end of the process PDF.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SIGNATURE_ID_RE = re.compile(
    r"(?mi)Documento assinado eletronicamente por .+?\s*-\s*([0-9A-Za-z]{7})\b"
)

_ROW_RE = re.compile(
    r"(?m)^\s*([0-9A-Fa-f]{7})\s+(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2})\s+(.*)$"
)

# Column heuristic: Tipo usually starts around col 60+
_TIPO_COL = 60

FORCE_IMAGE_OCR_TIPOS = (
    "termo de rescisão de contrato de trabalho",
    "trct",
    "comunicação de dispensa e seguro desemprego",
    "cd/sd",
    "ficha de registro de empregado",
    "ficha de registro",
    "contracheque/recibo de salário",
    "contracheque/recibo",
    "contracheque",
    "recibo de salário",
    "extrato de fgts",
    "documento de identificação",
    "carteira de trabalho e previdência social",
    "ctps",
)

PETITION_TIPOS = (
    "petição inicial",
    "contestação",
    "réplica",
    "manifestação",
)

GENERIC_TIPOS = (
    "despacho",
    "intimação",
    "certidão",
    "ata da audiência",
    "documento diverso",
    "solicitação de habilitação",
)


@dataclass
class SumarioEntry:
    doc_id: str
    titulo: str
    tipo: str
    data_assinatura: Optional[str] = None

    @property
    def tipo_norm(self) -> str:
        return _normalize_tipo(self.tipo)

    def is_force_image_form(self) -> bool:
        t = self.tipo_norm
        title = _normalize_tipo(self.titulo)
        blob = f"{t} {title}"
        return any(key in blob for key in FORCE_IMAGE_OCR_TIPOS)

    def is_petition_like(self) -> bool:
        t = self.tipo_norm
        return any(key in t for key in PETITION_TIPOS)

    def is_generic(self) -> bool:
        t = self.tipo_norm
        if "documento diverso" in t:
            return True
        return any(key == t or t.startswith(key) for key in GENERIC_TIPOS)


def _normalize_tipo(tipo: str) -> str:
    text = (tipo or "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_signature_ids(text: str) -> List[str]:
    ids: List[str] = []
    seen = set()
    for match in SIGNATURE_ID_RE.finditer(text or ""):
        doc_id = match.group(1).lower()
        if doc_id not in seen:
            seen.add(doc_id)
            ids.append(doc_id)
    return ids


def parse_sumario_text(text: str) -> Dict[str, SumarioEntry]:
    """
    Parse SUMÁRIO table from pdftotext -layout output.

    Handles Tipo wrapped above/below the Id row (common in PJe exports).
    """
    if not text:
        return {}

    upper = text.upper()
    idx = upper.rfind("SUMÁRIO")
    if idx < 0:
        idx = upper.rfind("SUMARIO")
    if idx < 0:
        return {}

    chunk = text[idx:]
    header_match = re.search(r"(?mi)^\s*Id\.?\s+.*Tipo\s*$", chunk)
    if header_match:
        chunk = chunk[header_match.end() :]

    entries: Dict[str, SumarioEntry] = {}
    current: Optional[SumarioEntry] = None
    pending_tipo_prefix: List[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current.titulo = re.sub(r"\s+", " ", current.titulo).strip()
        current.tipo = re.sub(r"\s+", " ", current.tipo).strip()
        if not current.tipo:
            current.tipo = current.titulo
        entries[current.doc_id] = current
        current = None

    for raw_line in chunk.splitlines():
        # Preserve leading spaces for column detection; drop CR
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if re.match(r"(?i)^\s*fls\.?\s*:", line):
            continue
        if re.match(r"(?i)^\s*documento assinado eletronicamente", line):
            continue
        if re.match(r"(?i)^\s*data da\s*$", line.strip()):
            continue
        if re.match(r"(?i)^\s*assinatura\s*$", line.strip()):
            continue

        row = _ROW_RE.match(line)
        if row:
            flush()
            doc_id = row.group(1).lower()
            data = row.group(2).strip()
            rest = row.group(3)
            titulo, tipo_from_row = _split_rest_by_columns(rest, line)

            tipo_parts = list(pending_tipo_prefix)
            pending_tipo_prefix = []
            if tipo_from_row:
                tipo_parts.append(tipo_from_row)
            tipo = " ".join(tipo_parts).strip()

            current = SumarioEntry(
                doc_id=doc_id,
                titulo=titulo.strip(),
                tipo=tipo,
                data_assinatura=data,
            )
            continue

        stripped = line.strip()
        if not stripped:
            continue

        leading = len(line) - len(line.lstrip(" "))

        # Continuation / wrap lines (no Id)
        if current is not None and _tipo_looks_truncated(current.tipo) and (
            leading >= 40 or _looks_like_tipo_phrase(stripped)
        ):
            # Finish a truncated tipo (e.g. "Documento de" + "Identificação")
            current.tipo = f"{current.tipo} {stripped}".strip()
            continue

        if current is not None and leading >= 20 and leading < _TIPO_COL and not _looks_like_tipo_phrase(stripped):
            # Documento title wrap under middle column
            current.titulo = f"{current.titulo} {stripped}".strip()
            continue

        if current is not None and leading < 20 and not _looks_like_tipo_phrase(stripped):
            current.titulo = f"{current.titulo} {stripped}".strip()
            continue

        # Otherwise: tipo prefix for the NEXT Id row (even if current exists
        # and its tipo is already complete — e.g. "Documento de" before CNH).
        pending_tipo_prefix.append(stripped)

    flush()
    # If page-break left a dangling tipo prefix without consuming it, ignore
    logger.info("SUMÁRIO parseado: %s documentos", len(entries))
    return entries


def _split_rest_by_columns(rest: str, full_line: str) -> Tuple[str, str]:
    """
    Split the part after date into titulo (left) and tipo (right).

    Uses the original full line columns when possible.
    """
    # Find where 'rest' begins in full_line
    idx = full_line.find(rest)
    if idx < 0:
        parts = re.split(r"\s{2,}", rest.strip())
        if len(parts) >= 2:
            return " ".join(parts[:-1]), parts[-1]
        return rest.strip(), ""

    # Characters from start of rest that fall into Tipo column
    # Tipo column absolute index ≈ _TIPO_COL
    rel_tipo = max(0, _TIPO_COL - idx)
    if rel_tipo > 0 and rel_tipo < len(rest):
        left = rest[:rel_tipo].strip()
        right = rest[rel_tipo:].strip()
        if right:
            return left, right
        # No right text on this line — maybe tipo entirely in wraps
        return left, ""

    # Fallback: split on 2+ spaces
    parts = re.split(r"\s{2,}", rest.strip())
    if len(parts) >= 2:
        return " ".join(parts[:-1]), parts[-1]
    return rest.strip(), ""


def _looks_like_tipo_only_line(stripped: str, leading: int) -> bool:
    if leading >= _TIPO_COL:
        return True
    return _looks_like_tipo_phrase(stripped) and leading >= 30


def _looks_like_tipo_phrase(text: str) -> bool:
    low = text.lower().strip()
    phrases = (
        "documento de",
        "identificação",
        "declaração de",
        "hipossuficiência",
        "carteira de trabalho",
        "previdência social",
        "(ctps)",
        "convenção coletiva",
        "trabalho (cct)",
        "trabalho (act)",
        "ficha de registro",
        "empregado",
        "cartão de",
        "ponto/controle",
        "frequência",
        "contracheque",
        "recibo",
        "de salário",
        "termo de rescisão",
        "contrato de trabalho",
        "(trct)",
        "comunicação de",
        "dispensa e seguro",
        "desemprego (cd/sd)",
        "acordo coletivo",
        "solicitação de",
        "habilitação",
        "substabelecimento",
        "com reserva de",
        "poderes",
    )
    return any(p in low for p in phrases) or (
        len(low.split()) <= 4 and leading_tipo_word(low)
    )


def leading_tipo_word(low: str) -> bool:
    starts = (
        "documento",
        "declaração",
        "carteira",
        "convenção",
        "ficha",
        "cartão",
        "contracheque",
        "termo",
        "comunicação",
        "dispensa",
        "desemprego",
        "acordo",
        "solicitação",
        "identificação",
        "empregado",
        "previdência",
        "trabalho",
        "recibo",
        "frequência",
        "substabelecimento",
        "poderes",
    )
    return any(low.startswith(s) for s in starts)


def _tipo_looks_truncated(tipo: str) -> bool:
    t = (tipo or "").strip().lower()
    if not t:
        return True
    trunc_ends = (
        " de",
        " do",
        " da",
        " e",
        "registro de",
        "contrato de",
        "dispensa e",
        "seguro",
        "trabalho e",
        "previdência",
        "recibo",
        "documento de",
        "comunicação de",
        "declaração de",
        "carteira de",
        "convenção coletiva de",
        "acordo coletivo de",
        "solicitação de",
        "cartão de",
        "ponto/controle de",
        "com reserva de",
    )
    return any(t.endswith(s) for s in trunc_ends)


def parse_sumario_from_pages(page_texts: Sequence[str]) -> Dict[str, SumarioEntry]:
    if not page_texts:
        return {}
    tail = page_texts[-min(40, len(page_texts)) :]
    entries = parse_sumario_text("\n".join(tail))
    if entries:
        return entries
    return parse_sumario_text("\n".join(page_texts))


def resolve_entry_for_page(
    signature_ids: Iterable[str],
    sumario: Dict[str, SumarioEntry],
) -> Optional[SumarioEntry]:
    matches: List[SumarioEntry] = []
    for doc_id in signature_ids:
        entry = sumario.get(doc_id.lower())
        if entry:
            matches.append(entry)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    for entry in matches:
        if entry.is_force_image_form():
            return entry
    for entry in matches:
        if not entry.is_generic() and not entry.is_petition_like():
            return entry
    for entry in matches:
        if not entry.is_generic():
            return entry
    return matches[0]


def detect_doc_kind_from_text(text: str) -> Optional[str]:
    t = (text or "").lower()
    if not t.strip():
        return None
    if "termo de homologação de rescisão" in t or (
        "termo de rescisão" in t and "contrato de trabalho" in t
    ) or re.search(r"\btrct\b", t):
        return "trct"
    if "comunicação de dispensa" in t:
        return "cd_sd"
    if "requerimento" in t and "seguro" in t and "desemprego" in t:
        return "cd_sd"
    if "seguro-desemprego" in t or "seguro desemprego" in t:
        return "cd_sd"
    if "ficha de registro de empregado" in t or "ficha de registro de empregados" in t:
        return "ficha_registro"
    if "recibo de pagamento" in t or "contracheque" in t:
        return "recibo"
    if "extrato" in t and "fgts" in t:
        return "fgts"
    if "carteira nacional de habilita" in t or "senatran" in t or (
        "habilita" in t and ("cnh" in t or "driver license" in t)
    ):
        return "cnh"
    if "documento de identificação" in t or "carteira de identidade" in t:
        return "identidade"
    return None


def tipo_to_kind(tipo: str, titulo: str = "") -> Optional[str]:
    blob = _normalize_tipo(f"{tipo} {titulo}")
    if not blob:
        return None
    if "trct" in blob or "termo de rescisão" in blob:
        return "trct"
    if "cd/sd" in blob or "comunicação de dispensa" in blob or (
        "seguro" in blob and "desemprego" in blob
    ):
        return "cd_sd"
    if "ficha de registro" in blob:
        return "ficha_registro"
    if "contracheque" in blob or "recibo de salário" in blob or "recibo de salario" in blob:
        return "recibo"
    if "extrato de fgts" in blob or ("fgts" in blob and "extrato" in blob):
        return "fgts"
    if "documento de identificação" in blob or (
        "identificação" in blob and "documento" in blob
    ):
        return "identidade"
    if "carteira de trabalho" in blob or re.search(r"\bctps\b", blob):
        return "ctps"
    return None


def load_sumario_for_pdf(
    pdf_path: Path | str,
    page_texts: Optional[Sequence[str]] = None,
) -> Dict[str, SumarioEntry]:
    from utils.pdf_pages import extract_pages_text

    if page_texts is None:
        page_texts = extract_pages_text(pdf_path)
    return parse_sumario_from_pages(page_texts)
