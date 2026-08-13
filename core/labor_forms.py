"""
Lightweight labor-document formatters.

Given OCR text + a document kind (from PJe SUMÁRIO Tipo or keyword fallback),
rebuild Markdown with field anchors / tables when possible.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


KIND_LABELS = {
    "trct": "Termo de Rescisão de Contrato de Trabalho (TRCT)",
    "cd_sd": "Comunicação de Dispensa e Seguro Desemprego (CD/SD)",
    "ficha_registro": "Ficha de Registro de Empregado",
    "recibo": "Contracheque / Recibo de Salário",
    "fgts": "Extrato de FGTS",
    "cnh": "Carteira Nacional de Habilitação (CNH)",
    "identidade": "Documento de Identificação",
    "ctps": "Carteira de Trabalho (CTPS)",
}


def refine_kind(kind: Optional[str], text: str) -> Optional[str]:
    """Narrow 'identidade' to CNH when OCR shows SENATRAN/CNH markers."""
    if not kind:
        return detect_from_ocr(text)
    if kind == "identidade":
        low = (text or "").lower()
        if "habilita" in low or "senatran" in low or "cnh" in low or "driver license" in low:
            return "cnh"
    return kind


def detect_from_ocr(text: str) -> Optional[str]:
    from core.pje_sumario import detect_doc_kind_from_text

    return detect_doc_kind_from_text(text)


def format_labor_document(
    text: str,
    kind: Optional[str],
    *,
    tables_markdown: Optional[List[str]] = None,
) -> str:
    """
    Return improved Markdown for a known labor form.

    Prefer Kreuzberg table markdown when provided; otherwise apply
    keyword/anchor formatting.
    """
    kind = refine_kind(kind, text)
    body = (text or "").strip()
    if not body and not tables_markdown:
        return ""

    parts: List[str] = []
    if kind and kind in KIND_LABELS:
        parts.append(f"**Tipo documental**: {KIND_LABELS[kind]}")
        parts.append("")

    if tables_markdown:
        parts.append("\n\n".join(t.strip() for t in tables_markdown if t and t.strip()))
        parts.append("")
        # Keep residual prose that is not already in tables (short header lines)
        residual = _strip_redundant_table_noise(body)
        if residual and len(residual) > 80:
            parts.append(residual)
        return "\n".join(parts).strip()

    if kind == "trct":
        parts.append(_format_trct(body))
    elif kind == "cd_sd":
        parts.append(_format_keyed_form(body, _CD_SD_KEYS))
    elif kind == "ficha_registro":
        parts.append(_format_keyed_form(body, _FICHA_KEYS))
    elif kind == "recibo":
        parts.append(_format_recibo(body))
    elif kind == "fgts":
        parts.append(_format_fgts(body))
    elif kind in {"cnh", "identidade"}:
        parts.append(_format_cnh(body))
    else:
        parts.append(body)

    return "\n".join(p for p in parts if p is not None).strip()


def _strip_redundant_table_noise(text: str) -> str:
    return text.strip()


def _format_keyed_form(text: str, keys: List[Tuple[str, str]]) -> str:
    """Extract label→value pairs into a Markdown list when possible."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    found: Dict[str, str] = {}
    used = set()

    for key_re, label in keys:
        for idx, line in enumerate(lines):
            if idx in used:
                continue
            match = re.search(key_re, line, flags=re.IGNORECASE)
            if not match:
                continue
            value = line[match.end() :].strip(" :.-|\t")
            if not value and idx + 1 < len(lines):
                nxt = lines[idx + 1]
                if not any(re.search(k, nxt, re.I) for k, _ in keys):
                    value = nxt
                    used.add(idx + 1)
            if value:
                found[label] = value
                used.add(idx)
            break

    if not found:
        return text

    out = ["| Campo | Valor |", "| --- | --- |"]
    for _, label in keys:
        if label in found:
            out.append(f"| {label} | {found[label]} |")
    out.append("")
    out.append("<details><summary>Texto OCR completo</summary>")
    out.append("")
    out.append(text)
    out.append("")
    out.append("</details>")
    return "\n".join(out)


_TRCT_FIELD_RE = re.compile(
    r"(?m)^\s*(\d{1,3})\s+([A-Za-zÀ-ú0-9][^:\n]{2,60}?)\s+(.+)$"
)


def _format_trct(text: str) -> str:
    rows = []
    for match in _TRCT_FIELD_RE.finditer(text):
        num, label, value = match.group(1), match.group(2).strip(), match.group(3).strip()
        rows.append((num, label, value))
    if len(rows) < 4:
        return _format_keyed_form(text, _TRCT_KEYS)

    out = [
        "| Nº | Campo | Valor |",
        "| --- | --- | --- |",
    ]
    for num, label, value in rows:
        out.append(f"| {num} | {label} | {value} |")
    out.append("")
    out.append("<details><summary>Texto OCR completo</summary>")
    out.append("")
    out.append(text)
    out.append("")
    out.append("</details>")
    return "\n".join(out)


def _format_recibo(text: str) -> str:
    """Try to rebuild vencimentos/descontos as a markdown table."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    table_rows: List[Tuple[str, str, str, str]] = []
    row_re = re.compile(
        r"^(\d{3,4})\s+(.+?)\s+([\d.,]+)?\s+([\d.,]+)?\s*$"
    )
    for line in lines:
        stripped = line.strip()
        match = row_re.match(stripped)
        if not match:
            continue
        code, desc, ref_or_venc, desc2 = match.groups()
        # Heuristic columns: código, descrição, vencimentos, descontos
        venc = ref_or_venc or ""
        desc_val = desc2 or ""
        table_rows.append((code, desc.strip(), venc, desc_val))

    header = []
    # Capture líquido / totais
    liquido = None
    for line in lines:
        m = re.search(r"Valor\s+L[ií]quido\s*([\d.,]+)", line, re.I)
        if m:
            liquido = m.group(1)

    if len(table_rows) >= 2:
        header = [
            "| Código | Descrição | Vencimentos | Descontos |",
            "| --- | --- | --- | --- |",
        ]
        for row in table_rows:
            header.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
        if liquido:
            header.append("")
            header.append(f"**Valor líquido**: {liquido}")
        header.append("")
        header.append("<details><summary>Texto OCR completo</summary>")
        header.append("")
        header.append(text)
        header.append("")
        header.append("</details>")
        return "\n".join(header)

    return text


def _format_fgts(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    meta_keys = [
        (r"Nome\s*:", "Nome"),
        (r"PIS", "PIS/PASEP/NIT"),
        (r"Empresa\s*:", "Empresa"),
        (r"CNPJ", "CNPJ"),
        (r"N[oº]?\s*Conta\s*FGTS", "Conta FGTS"),
        (r"Data\s*Admiss", "Data Admissão"),
        (r"SALDO\s*:", "Saldo"),
    ]
    meta = _format_keyed_form(text, meta_keys)

    hist_re = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?[\d.,]+)\s+(-?[\d.,]+)?\s*$"
    )
    rows = []
    for line in lines:
        m = hist_re.match(line)
        if m:
            rows.append(m.groups())

    if len(rows) >= 2:
        out = [meta, "", "### Histórico dos Lançamentos", ""]
        out.append("| Data | Descrição | Valor | Total |")
        out.append("| --- | --- | --- | --- |")
        for data, desc, valor, total in rows:
            out.append(f"| {data} | {desc} | {valor} | {total or ''} |")
        return "\n".join(out)
    return meta if meta != text else text


def _format_cnh(text: str) -> str:
    keys = [
        (r"Nome|NOME", "Nome"),
        (r"CPF", "CPF"),
        (r"Registro|REGISTRO", "Registro"),
        (r"Validade|VALIDADE", "Validade"),
        (r"Categoria|CATEGORIA", "Categoria"),
        (r"Nascimento|NASCIMENTO|Data.?Nasc", "Data de nascimento"),
        (r"Filia[cç][aã]o|FILIA", "Filiação"),
        (r"DOC\.?\s*IDENTIDADE|RG", "Documento de identidade"),
    ]
    # CNH OCR is noisy; still attempt structured fields
    return _format_keyed_form(text, keys)


_TRCT_KEYS = [
    (r"CNPJ|CEI", "CNPJ/CEI"),
    (r"Raz[aã]o\s*Social|Nome", "Empregador"),
    (r"PIS|PASEP", "PIS/PASEP"),
    (r"\bCPF\b", "CPF"),
    (r"Data\s*de\s*Admiss", "Data de Admissão"),
    (r"Data\s*do\s*Afastamento|Data\s*de\s*Afastamento", "Data do Afastamento"),
    (r"Causa\s*do\s*Afastamento", "Causa do Afastamento"),
]

_CD_SD_KEYS = [
    (r"NOME\b", "Nome"),
    (r"NOME\s*DA\s*M[ÃA]E", "Nome da Mãe"),
    (r"\bCPF\b", "CPF"),
    (r"CTPS", "CTPS"),
    (r"DATA\s*ADMISS", "Data Admissão"),
    (r"DATA\s*DISPENSA", "Data Dispensa"),
    (r"AVISO\s*PR[ÉE]VIO", "Aviso Prévio"),
]

_FICHA_KEYS = [
    (r"^Nome\b|Nome\s", "Nome"),
    (r"Raz[aã]o\s*Social", "Razão Social"),
    (r"CNPJ", "CNPJ"),
    (r"\bCPF\b", "CPF"),
    (r"Admiss", "Admissão"),
    (r"Cargo", "Cargo"),
    (r"PIS", "PIS"),
]
