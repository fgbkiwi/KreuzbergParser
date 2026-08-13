"""Deterministic labor-form extractors (LlamaParse-style Markdown).

Uses Tesseract word boxes + optional OpenCV table grids. When a template
cannot fill enough required fields, callers may fall back to a VLM.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from core.form_layout import (
    Line,
    Word,
    detect_table_grid,
    grid_to_html,
    group_lines,
    line_text_in_x_range,
    normalize_ocr_text,
    ocr_words,
    rows_to_html,
    words_to_plain_text,
)
from core.pje_sumario import detect_doc_kind_from_text

logger = logging.getLogger(__name__)

TEMPLATE_KINDS = {
    "trct",
    "ficha_registro",
    "recibo",
    "fgts",
    "cd_sd",
    "cnh",
    "identidade",
    "ctps",
}

_MONEY_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
_DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_NUM_TOKEN_RE = re.compile(r"^\d{1,3}(?:[.,]\d)?$")
_SECTION_RE = re.compile(
    r"(identifica[cç][aã]o\s+do\s+empregador|"
    r"identifica[cç][aã]o\s+do\s+trabalhador|"
    r"dados\s+do\s+contrato|"
    r"discrimina[cç][aã]o\s+das\s+verbas|"
    r"termo\s+de\s+homologa|"
    r"dedu[cç][oõ]es|"
    r"descontos)\b",
    re.I,
)


@dataclass
class ExtractionResult:
    kind: str
    markdown: str
    source: str
    ok: bool
    fill_ratio: float
    fields: Dict[str, str] = field(default_factory=dict)
    n_table_rows: int = 0
    reason: str = ""


def try_structured_extraction(
    png_bytes: bytes,
    *,
    kind: Optional[str] = None,
    ocr_text: str = "",
    min_fill_ratio: float = 0.45,
    enable_vlm: bool = False,
    vlm_base_url: Optional[str] = None,
    vlm_model: Optional[str] = None,
) -> Optional[ExtractionResult]:
    """Template first; optional VLM fallback. Returns None if both fail."""
    result = extract_with_template(
        png_bytes, kind=kind, ocr_text=ocr_text, min_fill_ratio=min_fill_ratio
    )
    if result and result.ok:
        return result

    resolved = (result.kind if result else None) or kind
    if enable_vlm and resolved in TEMPLATE_KINDS:
        try:
            from core.vlm_ocr import parse_page_image

            vlm_md = parse_page_image(
                png_bytes, base_url=vlm_base_url, model=vlm_model
            )
        except Exception as exc:
            logger.warning("VLM fallback failed: %s", exc)
            vlm_md = None
        if vlm_md and vlm_md.strip():
            wrapped = _with_ocr_details(vlm_md.strip(), ocr_text)
            return ExtractionResult(
                kind=kind or detect_form_kind(vlm_md, kind) or "unknown",
                markdown=wrapped,
                source="vlm",
                ok=True,
                fill_ratio=1.0,
                reason="vlm",
            )

    return result


def extract_with_template(
    png_bytes: bytes,
    *,
    kind: Optional[str] = None,
    ocr_text: str = "",
    min_fill_ratio: float = 0.45,
) -> Optional[ExtractionResult]:
    words = ocr_words(png_bytes)
    plain = normalize_ocr_text(ocr_text or words_to_plain_text(words))
    resolved = detect_form_kind(plain, kind)
    if not resolved or resolved not in TEMPLATE_KINDS:
        return ExtractionResult(
            kind=resolved or kind or "",
            markdown="",
            source="template",
            ok=False,
            fill_ratio=0.0,
            reason="unknown_kind",
        )
    if not words and not plain:
        return ExtractionResult(
            kind=resolved,
            markdown="",
            source="template",
            ok=False,
            fill_ratio=0.0,
            reason="empty_ocr",
        )

    lines = group_lines(words) if words else _lines_from_plain(plain)
    if resolved == "trct":
        result = _extract_trct(lines, words, png_bytes, plain)
    elif resolved == "ficha_registro":
        result = _extract_ficha(lines, words, png_bytes, plain)
    elif resolved == "recibo":
        result = _extract_recibo(lines, words, png_bytes, plain)
    elif resolved == "fgts":
        result = _extract_fgts(lines, words, png_bytes, plain)
    else:
        result = _extract_generic_kv(lines, resolved, plain)

    if result.markdown:
        result.markdown = _with_ocr_details(result.markdown, plain)
    result.ok = result.fill_ratio >= min_fill_ratio and bool(result.markdown.strip())
    if not result.ok and not result.reason:
        result.reason = f"fill_ratio={result.fill_ratio:.2f}<{min_fill_ratio:.2f}"
    return result


def detect_form_kind(text: str, hint: Optional[str] = None) -> Optional[str]:
    low = (text or "").lower()
    if "termo de homologação" in low or "termo de homologacao" in low:
        return "trct"
    detected = detect_doc_kind_from_text(text)
    if hint == "identidade" and detected == "cnh":
        return "cnh"
    return detected or hint


# ---------------------------------------------------------------------------
# TRCT / homologação
# ---------------------------------------------------------------------------

_TRCT_REQUIRED = ("01", "02", "10", "11", "24", "26")
_TRCT_VERBA_START = 50


def _extract_trct(
    lines: List[Line],
    words: Sequence[Word],
    png_bytes: bytes,
    plain: str,
) -> ExtractionResult:
    is_homolog = bool(
        re.search(r"termo\s+de\s+homologa", plain, re.I)
        and not re.search(r"discrimina[cç][aã]o\s+das\s+verbas", plain, re.I)
    )
    fields = _pair_numbered_fields(lines, max_num=49)
    verbas, deducoes = _extract_trct_tables(lines, words, png_bytes, plain)

    filled = {k: v for k, v in fields.items() if v and not _looks_like_label_only(v)}
    required_hit = sum(1 for k in _TRCT_REQUIRED if k in filled)
    fill_ratio = required_hit / max(1, len(_TRCT_REQUIRED))
    if verbas:
        fill_ratio = max(fill_ratio, min(1.0, 0.4 + 0.05 * len(verbas)))
    if is_homolog and filled:
        fill_ratio = max(fill_ratio, 0.55)

    parts: List[str] = []
    if is_homolog:
        parts.append("# TERMO DE HOMOLOGAÇÃO DE RESCISÃO DO CONTRATO DE TRABALHO")
        parts.append("")
        parts.append("## EMPREGADOR")
        parts.extend(_kv_md(fields, ("01", "02")))
        parts.append("")
        parts.append("## TRABALHADOR")
        parts.extend(_kv_md(fields, ("10", "11", "17", "18", "19", "20")))
        parts.append("")
        parts.extend(_kv_md(fields, ("22", "24", "25", "26", "27", "29", "30", "31", "32")))
        prose = _homolog_prose(plain)
        if prose:
            parts.append("")
            parts.append(prose)
    else:
        parts.append("# TERMO DE RESCISÃO DO CONTRATO DE TRABALHO")
        parts.append("")
        parts.append("## IDENTIFICAÇÃO DO EMPREGADOR")
        parts.append("")
        parts.extend(_kv_md(fields, ("01", "02", "03", "04", "05", "06", "07", "08", "09")))
        parts.append("")
        parts.append("## IDENTIFICAÇÃO DO TRABALHADOR")
        parts.append("")
        parts.extend(
            _kv_md(
                fields,
                ("10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20"),
            )
        )
        parts.append("")
        parts.append("## DADOS DO CONTRATO")
        parts.append("")
        parts.extend(
            _kv_md(
                fields,
                (
                    "21",
                    "22",
                    "23",
                    "24",
                    "25",
                    "26",
                    "27",
                    "28",
                    "29",
                    "30",
                    "31",
                    "32",
                ),
            )
        )
        if verbas:
            parts.append("")
            parts.append("## DISCRIMINAÇÃO DAS VERBAS RESCISÓRIAS")
            parts.append("")
            parts.append("### Verbas Rescisórias")
            parts.append("")
            parts.append(_verbas_html(verbas))
        if deducoes:
            parts.append("")
            parts.append("### Deduções")
            parts.append("")
            parts.append(_deducoes_html(deducoes))
        footer = _trct_footer(plain)
        if footer:
            parts.append("")
            parts.append(footer)

    md = "\n".join(p for p in parts if p is not None).strip()
    return ExtractionResult(
        kind="trct",
        markdown=md,
        source="template",
        ok=False,
        fill_ratio=fill_ratio,
        fields={f"{n} {_field_label(n, lines)}".strip(): v for n, v in filled.items()},
        n_table_rows=len(verbas) + len(deducoes),
    )


def _pair_numbered_fields(lines: Sequence[Line], max_num: int = 49) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    labels_meta: List[Tuple[int, List[Dict]]] = []

    for i, line in enumerate(lines):
        parsed = _numbered_labels_on_line(line)
        parsed = [f for f in parsed if _num_key(f["num"]) <= max_num]
        if not parsed:
            continue
        labels_meta.append((i, parsed))
        for item in parsed:
            key = _num_key_str(item["num"])
            # Value may sit on the same line to the right of the label
            same = line_text_in_x_range(line, item["x2"] + 4, item["x_end"] - 4)
            if same and not _looks_like_label_only(same) and not _is_numbered_label_text(same):
                fields[key] = same

    for idx, (line_i, parsed) in enumerate(labels_meta):
        next_label_line = (
            labels_meta[idx + 1][0] if idx + 1 < len(labels_meta) else min(len(lines), line_i + 4)
        )
        stop = min(next_label_line, line_i + 4)
        for item in parsed:
            key = _num_key_str(item["num"])
            if fields.get(key):
                continue
            chunks: List[str] = []
            for j in range(line_i + 1, stop):
                chunk = line_text_in_x_range(lines[j], item["x_start"], item["x_end"])
                if not chunk:
                    continue
                if _SECTION_RE.search(chunk) or _is_numbered_label_text(chunk):
                    break
                chunks.append(chunk)
            if chunks:
                fields[key] = " ".join(chunks).strip()

        # Plain-text fallback: next line has no usable x-alignment (token-index geometry)
        missing = [item for item in parsed if not fields.get(_num_key_str(item["num"]))]
        if missing and line_i + 1 < len(lines) and line_i + 1 < stop:
            nxt = lines[line_i + 1].text.strip()
            if nxt and not _is_numbered_label_text(nxt) and not _SECTION_RE.search(nxt):
                values = _split_value_line(nxt, len(parsed))
                if len(values) == len(parsed):
                    for item, value in zip(parsed, values):
                        key = _num_key_str(item["num"])
                        if not fields.get(key) and value.strip():
                            fields[key] = value.strip()
    return fields


_ATOMIC_VALUE_RE = re.compile(
    r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"
    r"|\d{3}\.\d{3}\.\d{3}-\d{2}"
    r"|\d{2}/\d{2}/\d{4}"
    r"|-?\d{1,3}(?:\.\d{3})*,\d{2}"
    r"|\d{5}-?\d{3}"
    r"|\d{4}-\d/\d{2}"
    r"|SJ\d+"
    r"|\d{2}\s*-\s*Empregado"
    r"|\d+\.\d{3}\.\d{3}\.\d{5,}"
    r"|Quadra\s+\S+(?:\s+\S+){0,12}"
)


def _split_value_line(text: str, n_labels: int) -> List[str]:
    """Split a value row into ``n_labels`` cells when bounding boxes are absent."""
    text = (text or "").strip()
    if n_labels <= 1:
        return [text]
    wide = [p for p in re.split(r"\s{2,}", text) if p.strip()]
    if len(wide) == n_labels:
        return wide
    if len(wide) > n_labels:
        return wide[: n_labels - 1] + [" ".join(wide[n_labels - 1 :])]

    atoms: List[str] = []
    cursor = 0
    for match in _ATOMIC_VALUE_RE.finditer(text):
        gap = text[cursor : match.start()].strip()
        if gap:
            atoms.append(gap)
        atoms.append(match.group(0).strip())
        cursor = match.end()
    tail = text[cursor:].strip()
    if tail:
        atoms.append(tail)
    atoms = [a for a in atoms if a]
    if len(atoms) == n_labels:
        return atoms
    if len(atoms) == n_labels - 1:
        return atoms + [""]
    if len(atoms) > n_labels:
        return atoms[: n_labels - 1] + [" ".join(atoms[n_labels - 1 :])]
    return atoms


def _numbered_labels_on_line(line: Line) -> List[Dict]:
    words = line.words
    fields: List[Dict] = []
    i = 0
    while i < len(words):
        token = words[i].text.replace(",", ".")
        glued = re.match(r"^(\d{1,3}(?:\.\d)?)([A-Za-zÀ-ú(].+)$", words[i].text)
        if _NUM_TOKEN_RE.match(token) and i + 1 < len(words) and re.match(
            r"^[A-Za-zÀ-ú(]", words[i + 1].text
        ):
            label_words = [words[i + 1]]
            j = i + 2
            while j < len(words) and not _NUM_TOKEN_RE.match(words[j].text.replace(",", ".")):
                label_words.append(words[j])
                j += 1
            fields.append(
                {
                    "num": token,
                    "label": " ".join(w.text for w in label_words),
                    "x_start": words[i].x,
                    "x2": label_words[-1].x2,
                    "x_end": words[-1].x2 + 80,
                }
            )
            i = j
        elif glued and re.match(r"^[A-Za-zÀ-ú(]", glued.group(2)):
            fields.append(
                {
                    "num": glued.group(1),
                    "label": glued.group(2),
                    "x_start": words[i].x,
                    "x2": words[i].x2,
                    "x_end": words[-1].x2 + 80,
                }
            )
            i += 1
        else:
            i += 1
    for k, item in enumerate(fields):
        if k + 1 < len(fields):
            item["x_end"] = fields[k + 1]["x_start"]
        item["x_end"] = max(item["x_end"], item["x2"] + 40)
    return fields


def _extract_trct_tables(
    lines: Sequence[Line],
    words: Sequence[Word],
    png_bytes: bytes,
    plain: str,
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]]]:
    """Return (verbas, deducoes) as lists of (num, label, value)."""
    grid = detect_table_grid(png_bytes, words, min_rows=3, min_cols=4)
    verbas: List[Tuple[str, str, str]] = []
    deducoes: List[Tuple[str, str, str]] = []
    if grid:
        for row in grid.cells:
            texts = [c.text.strip() for c in row if c.text.strip()]
            parsed = _parse_rubrica_chunks(" | ".join(texts))
            for num, label, value in parsed:
                bucket = deducoes if _num_key(num) >= 100 else verbas
                bucket.append((num, label, value))
        if len(verbas) + len(deducoes) >= 4:
            return verbas, deducoes

    started = False
    for line in lines:
        text = line.text
        if re.search(r"discrimina[cç][aã]o\s+das\s+verbas|verbas\s+rescis", text, re.I):
            started = True
            continue
        if not started and re.search(r"^\s*5\d(\.\d)?\b", text):
            started = True
        if not started:
            continue
        if re.search(r"registro\s*:|digitally\s+signed|documento\s+assinado", text, re.I):
            break
        for num, label, value in _parse_rubrica_chunks(text):
            bucket = deducoes if _num_key(num) >= 100 else verbas
            bucket.append((num, label, value))
    # Totals from plain text if missing
    _inject_total(verbas, plain, r"TOTAL\s+BRUTO\s*[|]?\s*([\d.]+,\d{2})", "TOTAL BRUTO")
    _inject_total(deducoes, plain, r"TOTAL\s+DEDU[CÇ][OÕ]ES\s*[|]?\s*([\d.]+,\d{2})", "TOTAL DEDUÇÕES")
    _inject_total(deducoes, plain, r"VALOR\s+L[IÍ]QUIDO\s*[|]?\s*([\d.]+,\d{2})", "VALOR LÍQUIDO")
    return verbas, deducoes


def _parse_rubrica_chunks(text: str) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    segments = re.split(r"\s*\|\s*", text or "")
    pattern = re.compile(
        r"(?<!\d)(\d{2,3}(?:\.\d)?)\s+(.+?)\s+(-?[\d.]+,\d{2})\s*$"
    )
    for segment in segments:
        chunk = segment.strip()
        if not chunk:
            continue
        match = pattern.search(chunk)
        if not match:
            continue
        num, label, value = match.group(1), match.group(2).strip(" |-"), match.group(3)
        if _num_key(num) < _TRCT_VERBA_START and _num_key(num) < 100:
            continue
        label = re.sub(r"\s+", " ", label).strip(" .")
        out.append((num, label, value))
    return out


def _inject_total(
    bucket: List[Tuple[str, str, str]], plain: str, pattern: str, label: str
) -> None:
    if any(label.lower() in (item[1] or "").lower() for item in bucket):
        return
    match = re.search(pattern, plain, re.I)
    if match:
        bucket.append(("", label, match.group(1)))


def _verbas_html(verbas: Sequence[Tuple[str, str, str]]) -> str:
    headers = ["Rubrica", "Valor", "Rubrica", "Valor", "Rubrica", "Valor"]
    rows: List[List[str]] = []
    triples = [verbas[i : i + 3] for i in range(0, len(verbas), 3)]
    for group in triples:
        row: List[str] = []
        for num, label, value in group:
            name = f"{num} {label}".strip() if num else label
            row.extend([name, value])
        while len(row) < 6:
            row.append("")
        rows.append(row)
    return rows_to_html(headers, rows)


def _deducoes_html(deducoes: Sequence[Tuple[str, str, str]]) -> str:
    headers = ["Descontos", "Valor", "Descontos", "Valor", "Descontos", "Valor"]
    rows: List[List[str]] = []
    triples = [deducoes[i : i + 3] for i in range(0, len(deducoes), 3)]
    for group in triples:
        row: List[str] = []
        for num, label, value in group:
            name = f"{num} {label}".strip() if num else label
            row.extend([name, value])
        while len(row) < 6:
            row.append("")
        rows.append(row)
    return rows_to_html(headers, rows)


def _trct_footer(plain: str) -> str:
    match = re.search(
        r"Registro\s*:\s*(\S+).*?Cargo\s*:\s*(.+?)\s*\|\s*Setor\s*:\s*(.+?)\s*\|\s*Conta\s*Corrente\s*:\s*(.+)",
        plain,
        re.I | re.S,
    )
    if not match:
        match = re.search(
            r"Registro:\s*(\S+)\s*\|\s*Cargo\s*:\s*(.+?)\s*\|\s*Setor\s*:\s*(.+?)\s*\|\s*Conta Corrente\s*:\s*(.+)",
            plain,
            re.I,
        )
    if not match:
        return ""
    registro, cargo, setor, conta = (g.strip(" |") for g in match.groups())
    return (
        f"**Registro**: {registro} | **Cargo**: {cargo} | "
        f"**Setor**: {setor} | **Conta Corrente**: {conta}"
    )


def _homolog_prose(plain: str) -> str:
    match = re.search(
        r"(Foi prestada[\s\S]+?Instru[cç][aã]o\s+Normativa[^\n]+)",
        plain,
        re.I,
    )
    return match.group(1).strip() if match else ""


def _field_label(num: str, lines: Sequence[Line]) -> str:
    for line in lines:
        for item in _numbered_labels_on_line(line):
            if _num_key_str(item["num"]) == num:
                return str(item["label"]).strip()
    return ""


def _kv_md(fields: Dict[str, str], numbers: Sequence[str]) -> List[str]:
    out = []
    for num in numbers:
        value = (fields.get(num) or "").strip()
        label = {
            "01": "CNPJ/CEI",
            "02": "Razão Social / Nome",
            "03": "Endereço (logradouro, n°, andar, apartamento)",
            "04": "Bairro",
            "05": "Município",
            "06": "UF",
            "07": "CEP",
            "08": "CNAE",
            "09": "CNPJ/CEI Tomador/Obra",
            "10": "PIS/PASEP",
            "11": "Nome",
            "12": "Endereço (logradouro, n°, andar, apartamento)",
            "13": "Bairro",
            "14": "Município",
            "15": "UF",
            "16": "CEP",
            "17": "CTPS (n°, série, UF)",
            "18": "CPF",
            "19": "Data de Nascimento",
            "20": "Nome da Mãe",
            "21": "Tipo de Contrato",
            "22": "Causa do Afastamento",
            "23": "Remuneração Mês Ant.",
            "24": "Data de Admissão",
            "25": "Data do Aviso Prévio",
            "26": "Data do Afastamento",
            "27": "Cód. Afastamento",
            "28": "Pensão Alim. (%) TRCT",
            "29": "Pensão Alim (%) FGTS",
            "30": "Categoria do Trabalhador",
            "31": "Código Sindical",
            "32": "CNPJ e Nome da Entidade Sindical Laboral",
        }.get(num, num)
        # Prefer OCR label if we stored "num label" elsewhere — keep canonical.
        out.append(f"**{num} {label}**: {value}")
    return out


# ---------------------------------------------------------------------------
# Ficha de Registro
# ---------------------------------------------------------------------------

_FICHA_LABELS = [
    "Razão Social",
    "Cód Município",
    "Cod Município",
    "Cód Atividade",
    "Cod Atividade",
    "CNPJ/CPF",
    "Dados Pessoais",
    "Naturalidade",
    "Nacionalidade",
    "Estado Civil",
    "Nascimento",
    "Instrução",
    "Instrucao",
    "Deficiencia",
    "Deficiência",
    "Título Eleitor",
    "Titulo Eleitor",
    "Conta Corrente",
    "Habilitação",
    "Habilitacao",
    "Vencimento",
    "Categoria",
    "Admissão",
    "Admissao",
    "Periculosidade",
    "Insalubrid",
    "Transferência",
    "Transferencia",
    "Transferido",
    "Observações",
    "Observacoes",
    "eSocial",
    "Telefones",
    "Reservista",
    "Cônjuge",
    "Conjuge",
    "Genero",
    "Gênero",
    "Empregador",
    "Endereço",
    "Endereco",
    "Data Cadastro",
    "Última Atualização",
    "Ultima Atualizacao",
    "Nome",
    "Pai",
    "Mae",
    "Mãe",
    "Raça",
    "Raca",
    "Sexo",
    "CTPS",
    "Série",
    "Serie",
    "Emissão",
    "Emissao",
    "UF",
    "RG",
    "Orgão",
    "Orgao",
    "Órgão",
    "CPF",
    "PIS",
    "Zona",
    "Seção",
    "Secao",
    "Cargo",
    "CBO",
    "Setor",
    "Cipa",
]

_FICHA_SECTIONS = {
    "dados pessoais": "Dados Pessoais",
    "documentos": "Documentos",
    "dados funcionais": "Dados Funcionais",
    "alterações de salários": "Alterações de Salários",
    "alteracoes de salarios": "Alterações de Salários",
    "alterações de cargo": "Alterações de Cargo",
    "alteracoes de cargo": "Alterações de Cargo",
    "alterações de sindicato": "Alterações de Sindicato",
    "alteracoes de sindicato": "Alterações de Sindicato",
    "alterações de local": "Alterações de Local de Trabalho",
    "alteracoes de local": "Alterações de Local de Trabalho",
    "férias": "Férias",
    "ferias": "Férias",
    "rescisão": "Rescisão do Contrato de Trabalho",
    "rescisao": "Rescisão do Contrato de Trabalho",
}

_FICHA_REQUIRED = ("Nome", "CPF", "Admissão")


def _extract_ficha(
    lines: List[Line],
    words: Sequence[Word],
    png_bytes: bytes,
    plain: str,
) -> ExtractionResult:
    pairs = _label_value_pairs(lines, _FICHA_LABELS)
    tables = _extract_named_tables(lines, _FICHA_TABLE_SPECS)
    if not any(title == "Alterações de Salários" for title, _, _ in tables):
        salary_rows = _parse_ficha_salary_rows(plain)
        if salary_rows:
            tables.append(
                (
                    "Alterações de Salários",
                    rows_to_html(
                        ["Descrição", "Referencia", "Vigência", "Valor", "Motivo", "Última Atualização"],
                        salary_rows,
                    ),
                    len(salary_rows),
                )
            )

    nome = pairs.get("Nome") or ""
    parts = ["# Ficha de Registro de Empregados", ""]
    if nome:
        extra_id = ""
        id_match = re.search(r"\b(\d{5,8})\b", nome)
        if id_match and id_match.group(1) not in nome.split()[0:2]:
            extra_id = ""
        parts.append(f"**Nome**: {nome}")
        parts.append("")

    if any(k in pairs for k in ("Razão Social", "CNPJ/CPF", "Endereço")):
        parts.append("**Empregador**")
        parts.append("")
        for key in ("Razão Social", "Endereço", "Cód Município", "CNPJ/CPF", "Cód Atividade"):
            if key in pairs:
                parts.append(f"**{key}**: {pairs[key]}")
        parts.append("")

    section_order = [
        ("Dados Pessoais", ("Pai", "Mae", "Cônjuge", "Nascimento", "Raça", "Naturalidade",
                            "Nacionalidade", "Instrução", "Estado Civil", "Sexo", "Genero",
                            "Deficiencia")),
        ("Documentos", ("CTPS", "Série", "Emissão", "UF", "RG", "Orgão", "CPF", "PIS",
                        "Data Cadastro", "Título Eleitor", "Zona", "Seção", "Reservista",
                        "Endereço", "Telefones", "Conta Corrente", "Habilitação",
                        "Categoria", "Vencimento")),
        ("Dados Funcionais", ("Admissão", "Cargo", "CBO", "Setor", "eSocial",
                              "Periculosidade", "Insalubrid", "Transferência", "Cipa",
                              "Transferido", "Observações")),
    ]
    used_endereco = False
    for title, keys in section_order:
        present = []
        for key in keys:
            actual = _first_present(pairs, key)
            if actual is None:
                continue
            if key == "Endereço" and title == "Documentos" and not used_endereco:
                # first endereço already printed under empregador; keep personal address too
                if pairs.get("Endereço") and pairs["Endereço"] not in "\n".join(parts):
                    present.append((key, pairs[actual]))
                elif actual in pairs:
                    present.append((key, pairs[actual]))
                used_endereco = True
                continue
            present.append((key, pairs[actual]))
        if not present:
            continue
        parts.append(f"## {title}")
        parts.append("")
        for key, value in present:
            parts.append(f"**{key}**: {value}")
        parts.append("")

    n_table_rows = 0
    for title, html, nrows in tables:
        parts.append(f"## {title}")
        parts.append("")
        parts.append(html)
        parts.append("")
        n_table_rows += nrows

    required_hit = sum(1 for k in _FICHA_REQUIRED if _first_present(pairs, k))
    fill_ratio = required_hit / len(_FICHA_REQUIRED)
    if n_table_rows >= 3:
        fill_ratio = max(fill_ratio, 0.7)
    if len(pairs) >= 8:
        fill_ratio = max(fill_ratio, 0.6)

    return ExtractionResult(
        kind="ficha_registro",
        markdown="\n".join(parts).strip(),
        source="template",
        ok=False,
        fill_ratio=fill_ratio,
        fields=pairs,
        n_table_rows=n_table_rows,
    )


_SALARY_ROW_RE = re.compile(
    r"Sal[aá]rio\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+"
    r"([\d.]+,\d{2})\s+(.+?)\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})"
)


def _parse_ficha_salary_rows(plain: str) -> List[List[str]]:
    rows = []
    for match in _SALARY_ROW_RE.finditer(plain or ""):
        ref, vig, valor, motivo, atual = match.groups()
        rows.append(["Salário", ref, vig, valor, motivo.strip(), atual])
    return rows


_FICHA_TABLE_SPECS = [
    (
        "Alterações de Salários",
        ["Descrição", "Referencia", "Vigência", "Valor", "Motivo", "Última Atualização"],
        r"altera[cçg][oõa]es\s+de\s+sal",
    ),
    (
        "Alterações de Cargo",
        ["Início", "Descrição", "Motivo", "Última Atualização"],
        r"altera[cçg][oõa]es\s+de\s+cargo",
    ),
    (
        "Alterações de Sindicato",
        ["Início", "Descrição"],
        r"altera[cçg][oõa]es\s+de\s+sindicato",
    ),
    (
        "Alterações de Local de Trabalho",
        ["Início", "Local"],
        r"altera[cçg][oõa]es\s+de\s+local",
    ),
    (
        "Férias",
        ["Período Aquisitivo", "Período de Gozo", "Dias", "Abono", "Última Atualização"],
        r"^f[eé]rias\b",
    ),
]


# ---------------------------------------------------------------------------
# Recibo de pagamento
# ---------------------------------------------------------------------------

_RECIBO_EVENT_RE = re.compile(
    r"^(\d{4})\s+(.+?)\s+(-?[\d.]+,\d{2})\s*(-?[\d.]+,\d{2})?\s*$"
)
_RECIBO_EVENT_REF_RE = re.compile(
    r"^(\d{4})\s+(.+?)\s+([\d.,]+)\s+(-?[\d.]+,\d{2})\s*(-?[\d.]+,\d{2})?\s*$"
)


def _extract_recibo(
    lines: List[Line],
    words: Sequence[Word],
    png_bytes: bytes,
    plain: str,
) -> ExtractionResult:
    header = _label_value_pairs(
        lines,
        [
            "Empregador",
            "CNPJ",
            "Código",
            "Nome do Funcionário",
            "Nome",
            "CBO",
            "Cargo",
            "Processo",
            "Mês",
            "Mes",
        ],
    )
    events = _recibo_events(lines)
    if len(events) < 3:
        extra = _recibo_columnar_events(plain)
        if len(extra) > len(events):
            events = extra
    tot_pair = re.search(
        r"Total\s+Vencimentos\s+Total\s+Descontos\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})",
        plain,
        re.I,
    )
    totals = {
        "Total Vencimentos": (tot_pair.group(1) if tot_pair else "")
        or _search_money(plain, r"Total\s+Vencimentos\s*([\d.]+,\d{2})"),
        "Total Descontos": (tot_pair.group(2) if tot_pair else "")
        or _search_money(plain, r"Total\s+Descontos\s*([\d.]+,\d{2})"),
        "Valor Líquido": _search_money(
            plain, r"Valor\s+L[ií]quido\s*([\d.]+,\d{2})"
        ),
    }
    bases_headers = [
        "Salário Base",
        "Sal.Contr.INSS",
        "Base Calc. FGTS",
        "FGTS do Mês",
        "Base Calc. IRRF",
        "Faixa IRRF",
        "Folha",
    ]
    bases_row = _recibo_bases(lines, plain, bases_headers)

    empregador = header.get("Empregador") or _search_line_after(plain, r"Empregador")
    cnpj = header.get("CNPJ") or (_CNPJ_RE.search(plain).group(0) if _CNPJ_RE.search(plain) else "")
    nome = (
        header.get("Nome do Funcionário")
        or header.get("Nome")
        or ""
    )

    event_rows = []
    for code, desc, ref, venc, descnt in events:
        event_rows.append([code, desc, ref, venc, descnt, "", ""])

    inner_rows = []
    if event_rows:
        inner_rows.extend(event_rows)
    if totals.get("Total Vencimentos") or totals.get("Total Descontos"):
        inner_rows.append(
            [
                ("", 3),
                "Total Vencimentos",
                "Total Descontos",
                totals.get("Total Vencimentos") or "",
                totals.get("Total Descontos") or "",
            ]
        )
    if totals.get("Valor Líquido"):
        inner_rows.append(
            ["Mensagens", "", "", "Valor Líquido", totals["Valor Líquido"], "", ""]
        )

    table = ""
    if event_rows:
        # Flatten mixed colspan tuples for rows_to_html: use simple 5-col table
        simple_rows = [[c, d, r, v, ds] for c, d, r, v, ds, *_rest in
                       [(e[0], e[1], e[2], e[3], e[4]) for e in events]]
        if totals.get("Total Vencimentos"):
            simple_rows.append(
                [
                    "",
                    "Total Vencimentos / Descontos",
                    "",
                    totals.get("Total Vencimentos") or "",
                    totals.get("Total Descontos") or "",
                ]
            )
        if totals.get("Valor Líquido"):
            simple_rows.append(
                ["", "Valor Líquido", "", totals["Valor Líquido"], ""]
            )
        table = rows_to_html(
            ["Código", "Descrição", "Referência", "Vencimentos", "Descontos"],
            simple_rows,
            caption="Recibo de Pagamento de Salário",
        )

    bases_html = ""
    if bases_row and any(bases_row):
        bases_html = rows_to_html(bases_headers, [bases_row])

    parts = ["# Recibo de Pagamento de Salário", ""]
    if empregador:
        parts.append(f"**Empregador**: {empregador}")
    if cnpj:
        parts.append(f"**CNPJ**: {cnpj}")
    if nome:
        parts.append(f"**Nome do Funcionário**: {nome}")
    for key in ("Código", "CBO", "Cargo", "Processo", "Mês"):
        if header.get(key):
            parts.append(f"**{key}**: {header[key]}")
    parts.append("")
    if table:
        parts.append(table)
        parts.append("")
    if bases_html:
        parts.append(bases_html)

    event_money = sum(1 for e in events if e[3] or e[4])
    n_titles = len(re.findall(r"recibo de pagamento", plain, re.I))
    fill_ratio = 0.0
    if len(events) >= 3:
        fill_ratio = 0.7
    if totals.get("Valor Líquido") and cnpj:
        fill_ratio = max(fill_ratio, 0.8)
    if nome and cnpj:
        fill_ratio = max(fill_ratio, 0.5)
    if len(events) >= 1:
        fill_ratio = max(fill_ratio, 0.45)
    # Two payslips on one page with columnar OCR → prefer VLM fallback
    if n_titles >= 2 and event_money < 8:
        fill_ratio = min(fill_ratio, 0.30)
    if event_money < 3:
        fill_ratio = min(fill_ratio, 0.35)

    return ExtractionResult(
        kind="recibo",
        markdown="\n".join(parts).strip(),
        source="template",
        ok=False,
        fill_ratio=fill_ratio,
        fields={**header, **{k: v for k, v in totals.items() if v}},
        n_table_rows=len(events),
    )


_RECIBO_DESC_ROW_RE = re.compile(
    r"^(?:(\d{4})\s+)?([A-Za-zÀ-ú].+?)\s+"
    r"(?:([\d.,]+)\s+)?"
    r"(-?[\d.]+,\d{2})\s*"
    r"(-?[\d.]+,\d{2})?\s*$"
)


def _recibo_events(lines: Sequence[Line]) -> List[Tuple[str, str, str, str, str]]:
    events: List[Tuple[str, str, str, str, str]] = []
    started = False
    header_cols: Optional[List[Tuple[str, int, int]]] = None
    for i, line in enumerate(lines):
        low = _ocr_fold(line.text)
        if "descricao" in low and ("vencimento" in low or "referencia" in low or "desconto" in low):
            started = True
            header_cols = _match_headers_on_line(
                line, ["Código", "Descrição", "Referência", "Vencimentos", "Descontos"]
            ) or _match_headers_on_line(
                line, ["Descrição", "Referência", "Vencimentos", "Descontos"]
            )
            continue
        if re.search(r"c[oó]digo\s+descri", line.text, re.I):
            started = True
            continue
        if not started:
            if re.match(r"^\d{4}\b", line.text) or _RECIBO_DESC_ROW_RE.match(line.text):
                started = True
            else:
                continue
        if re.search(r"salario\s+base|total\s+vencimentos|mensagens|valor\s+l[ií]quido|sal\.contr", low):
            break
        if header_cols and len(header_cols) >= 3:
            cells = [line_text_in_x_range(line, a, b) for _, a, b in header_cols]
            amounts = [c for c in cells if _MONEY_RE.fullmatch(c or "")]
            if any(cells) and (amounts or cells[0]):
                while len(cells) < 5:
                    cells.append("")
                if header_cols[0][0].lower().startswith("cód") or header_cols[0][0].lower().startswith("cod"):
                    events.append((cells[0], cells[1], cells[2], cells[3], cells[4]))
                else:
                    events.append(("", cells[0], cells[1], cells[2], cells[3] if len(cells) > 3 else ""))
                continue
        text = line.text
        mref = _RECIBO_EVENT_REF_RE.match(text)
        if mref:
            code, desc, ref, a, b = mref.groups()
            venc, descnt = (a, b or "") if b else _split_venc_desc(desc, a)
            events.append((code, desc.strip(), ref, venc, descnt))
            continue
        m = _RECIBO_EVENT_RE.match(text)
        if m:
            code, desc, a, b = m.groups()
            events.append((code, desc.strip(), "", a, b or ""))
            continue
        mdesc = _RECIBO_DESC_ROW_RE.match(text)
        if mdesc:
            code, desc, ref, venc, descnt = mdesc.groups()
            events.append((code or "", desc.strip(), ref or "", venc, descnt or ""))
    return events


def _split_venc_desc(desc: str, amount: str) -> Tuple[str, str]:
    # Discount codes typically >= 5000
    return amount, ""


def _recibo_columnar_events(plain: str) -> List[Tuple[str, str, str, str, str]]:
    """Rebuild event rows when OCR dumps Descrição then Vencimentos as columns."""
    events: List[Tuple[str, str, str, str, str]] = []
    parts = re.split(r"(?i)recibo de pagamento", plain or "")
    for part in parts[1:]:
        desc_m = re.search(
            r"descri[cçg]\w*[^\n]*\n([\s\S]+?)(?:vencimentos|descontos|nome do|c[oó]digo|mensagens|total\s+venc|cbo\b)",
            part,
            re.I,
        )
        venc_m = re.search(
            r"vencimentos\s*\n((?:\s*-?[\d.]+,\d{2}\s*\n)+)",
            part,
            re.I,
        )
        desc_lines: List[str] = []
        if desc_m:
            for raw in desc_m.group(1).splitlines():
                line = raw.strip()
                if not line or _MONEY_RE.fullmatch(line):
                    continue
                if re.match(r"^\d{4}$", line):
                    continue
                if re.search(r"verisure|smart alarms|empregador|cnpj", line, re.I):
                    break
                desc_lines.append(line)
        amounts = _MONEY_RE.findall(venc_m.group(1)) if venc_m else []
        n = max(len(desc_lines), len(amounts))
        if n < 2:
            continue
        for i in range(n):
            desc = desc_lines[i] if i < len(desc_lines) else ""
            ref = ""
            m = re.match(r"^(.+?)\s+([\d.,]+)$", desc)
            if m and not _MONEY_RE.fullmatch(m.group(2) or ""):
                desc, ref = m.group(1).strip(), m.group(2)
            venc = amounts[i] if i < len(amounts) else ""
            if desc or venc:
                events.append(("", desc, ref, venc, ""))
    return events


def _recibo_bases(
    lines: Sequence[Line], plain: str, headers: Sequence[str]
) -> List[str]:
    for i, line in enumerate(lines):
        low = line.text.lower()
        if "base calc" in low and "fgts" in low:
            cols = _match_headers_on_line(line, list(headers))
            if cols and i + 1 < len(lines):
                return [line_text_in_x_range(lines[i + 1], a, b) for _, a, b in cols]
            nxt = lines[i + 1].text if i + 1 < len(lines) else ""
            amounts = _MONEY_RE.findall(nxt)
            if amounts:
                row = amounts[:6]
                extra = re.search(r"\b\d+/\d+\b", nxt)
                row.append(extra.group(0) if extra else "")
                while len(row) < len(headers):
                    row.append("")
                return row[: len(headers)]
    amounts = _MONEY_RE.findall(plain)
    return []


# ---------------------------------------------------------------------------
# Extrato FGTS
# ---------------------------------------------------------------------------

_FGTS_LABELS = [
    "PIS/PASEP/NIT",
    "CNPJ/CEI/CPF",
    "Cód. Estab.",
    "Cod. Estab.",
    "Nº Conta FGTS",
    "No Conta FGTS",
    "Data Admissão",
    "Data Admissao",
    "Data/Cód. Movimentação",
    "Data/Cod. Movimentacao",
    "Data Opção",
    "Data Opcao",
    "Taxa Juros",
    "Tipo Conta",
    "Valor Base para Fins Rescisórios",
    "Valor Base para Fins Rescisorios",
    "Atualizado em",
    "Categoria",
    "Empresa",
    "Nome",
    "Base",
    "SALDO",
    "Saldo",
]


def _extract_fgts(
    lines: List[Line],
    words: Sequence[Word],
    png_bytes: bytes,
    plain: str,
) -> ExtractionResult:
    pairs = _label_value_pairs(lines, _FGTS_LABELS)
    # Values often sit on the next visual line for this CAIXA layout
    if not pairs.get("Nome"):
        pairs.update(_kv_next_line(lines, _FGTS_LABELS))
    if not pairs.get("Nome"):
        pairs.update(_fgts_block_pairs(plain))

    hist = _fgts_history(lines, png_bytes, words)
    if len(hist) < 2:
        hist = _fgts_history_columnar(plain) or hist
    parts = ["# Extrato de Conta do Fundo de Garantia - FGTS", ""]
    for key in (
        "Nome",
        "PIS/PASEP/NIT",
        "Empresa",
        "CNPJ/CEI/CPF",
        "Cód. Estab.",
        "Categoria",
        "Nº Conta FGTS",
        "Data Admissão",
        "Data/Cód. Movimentação",
        "Data Opção",
        "Taxa Juros",
        "Tipo Conta",
        "Valor Base para Fins Rescisórios",
        "Base",
        "SALDO",
        "Atualizado em",
    ):
        actual = _first_present(pairs, key)
        if actual:
            parts.append(f"**{key}**: {pairs[actual]}")
    parts.append("")
    if hist:
        parts.append("## Histórico dos Lançamentos")
        parts.append("")
        parts.append(
            rows_to_html(
                ["Data", "Descrição dos Lançamentos", "Valor R$", "Total R$"],
                hist,
            )
        )

    fill_ratio = 0.0
    if _first_present(pairs, "Nome") or _first_present(pairs, "PIS/PASEP/NIT"):
        fill_ratio = 0.4
    if len(hist) >= 2:
        fill_ratio = max(fill_ratio, 0.8)
    if _first_present(pairs, "SALDO") and _first_present(pairs, "Nome"):
        fill_ratio = max(fill_ratio, 0.6)
    if len(hist) >= 5:
        fill_ratio = max(fill_ratio, 0.9)

    return ExtractionResult(
        kind="fgts",
        markdown="\n".join(parts).strip(),
        source="template",
        ok=False,
        fill_ratio=fill_ratio,
        fields=pairs,
        n_table_rows=len(hist),
    )


def _fgts_block_pairs(plain: str) -> Dict[str, str]:
    """CAIXA extrato dumps all labels then all values (column-wise OCR)."""
    pairs: Dict[str, str] = {}
    pis = re.search(r"\b(\d{3}\.\d{5}\.\d{2}-\d)\b", plain)
    if pis:
        pairs["PIS/PASEP/NIT"] = pis.group(1)
        before = plain[: pis.start()]
        names = re.findall(
            r"([A-ZÁ-Ú]{2,}(?:\s+[A-ZÁ-Ú][A-Za-zÁ-ú']+){1,6})", before
        )
        names = [n.strip() for n in names if "VERISURE" not in n.upper() and "CAIXA" not in n.upper()]
        if names:
            pairs["Nome"] = names[-1]
    cnpj = _CNPJ_RE.search(plain)
    if cnpj:
        pairs["CNPJ/CEI/CPF"] = cnpj.group(0)
    conta = re.search(r"\b(0000\d{6,})\b", plain)
    if conta:
        pairs["Nº Conta FGTS"] = conta.group(1)
    admis = re.search(
        r"Data Admiss\w*[^\d]{0,40}(\d{2}/\d{2}/\d{4})", plain, re.I
    )
    if admis:
        pairs["Data Admissão"] = admis.group(1)
    saldo = re.search(r"SALDO:\s*R?\$?\s*([\d.]+,\d{2}|0,00)", plain, re.I)
    if saldo:
        pairs["SALDO"] = saldo.group(1)
    return _canonicalize_keys(pairs)


def _fgts_history_columnar(plain: str) -> List[List[str]]:
    """Rebuild FGTS history when OCR reads Date / Desc / Valor / Total as columns."""
    if not re.search(r"hist[oó]rico\s+dos\s+lan", _ocr_simplify(plain)):
        return []
    dates = _DATE_RE.findall(plain)
    # Drop header/consultation timestamps: keep dates that look like movements (2025)
    move_dates = [
        d for d in dates
        if not d.startswith("08/04/2026")
        and re.search(r"/202[0-9]$", d)
    ]
    # Consultation date 08/04/2026 often appears; drop if it's the only 2026 before movements
    desc_block = re.search(
        r"descri[cçg]\w*\s+dos\s+lan[cç][\w\s]*?\n([\s\S]+?)(?:valor\s*r\$|total\s*r\$|#externo)",
        plain,
        re.I,
    )
    descriptions: List[str] = []
    if desc_block:
        for raw in desc_block.group(1).splitlines():
            line = raw.strip()
            if not line or _DATE_RE.fullmatch(line) or _MONEY_RE.fullmatch(line):
                continue
            if re.search(r"valor\s*r\$|total\s*r\$|https?://|documento\s+assinado", line, re.I):
                break
            descriptions.append(re.sub(r"\s+", " ", line))
    amounts = _MONEY_RE.findall(plain)
    # First total is usually SALDO ANTERIOR total; values follow
    # Heuristic: descriptions include SALDO ANTERIOR then N movements.
    if "SALDO ANTERIOR" in " ".join(descriptions).upper():
        n = len(descriptions)
        # totals: n values at the end of amount list often; valores: n-1 (no valor on saldo) + n totals
        totals = amounts[-n:] if len(amounts) >= n else amounts
        valores = amounts[-(2 * n - 1) : -n] if len(amounts) >= 2 * n - 1 else [""] + amounts[: max(0, n - 1)]
        if len(valores) < n:
            valores = [""] * (n - len(valores)) + valores
        rows = []
        date_iter = list(move_dates)
        # SALDO ANTERIOR has no date
        di = 0
        for i, desc in enumerate(descriptions):
            if "saldo anterior" in desc.lower():
                date = ""
            else:
                date = date_iter[di] if di < len(date_iter) else ""
                di += 1
            valor = valores[i] if i < len(valores) else ""
            total = totals[i] if i < len(totals) else ""
            rows.append([date, desc, valor, total])
        return rows
    return []


def _fgts_history(
    lines: Sequence[Line],
    png_bytes: bytes,
    words: Sequence[Word],
) -> List[List[str]]:
    grid = detect_table_grid(png_bytes, words, min_rows=3, min_cols=3)
    rows: List[List[str]] = []
    if grid and grid.n_cols >= 3:
        for i, row in enumerate(grid.cells):
            texts = [c.text.strip() for c in row]
            joined = " ".join(texts)
            if i == 0 and re.search(r"descri[cç][aã]o|lan[cç]amento", joined, re.I):
                continue
            date = next((t for t in texts if _DATE_RE.fullmatch(t)), "")
            amounts = [t for t in texts if _MONEY_RE.fullmatch(t)]
            desc_parts = [t for t in texts if t and t != date and t not in amounts]
            if desc_parts or amounts:
                valor = amounts[0] if amounts else ""
                total = amounts[1] if len(amounts) > 1 else ""
                rows.append([date, " ".join(desc_parts), valor, total])
        if len(rows) >= 2:
            return rows

    hist_re = re.compile(
        r"^(?:(\d{2}/\d{2}/\d{4})\s+)?(.+?)\s+(-?[\d.]+,\d{2})\s+(-?[\d.]+,\d{2})\s*$"
    )
    started = False
    fallback: List[List[str]] = []
    for line in lines:
        low = line.text.lower()
        if re.search(r"hist[oó]rico\s+dos\s+lan[cç]amentos|descri[cç][aã]o\s+dos\s+lan[cç]", low):
            started = True
            continue
        if re.search(r"externo|confidencial|imprimir|documento\s+assinado", low):
            if started:
                break
            continue
        m = hist_re.match(line.text)
        if m:
            date, desc, valor, total = m.groups()
            row = [date or "", desc.strip(), valor, total]
            if started:
                rows.append(row)
            else:
                fallback.append(row)
            continue
        if "saldo anterior" in low:
            amounts = _MONEY_RE.findall(line.text)
            row = ["", "SALDO ANTERIOR", "", amounts[-1] if amounts else ""]
            if started:
                rows.append(row)
            else:
                fallback.append(row)
    if len(rows) < 2 and len(fallback) >= 2:
        return fallback
    return rows


# ---------------------------------------------------------------------------
# Generic key/value (CNH, CD/SD, etc.)
# ---------------------------------------------------------------------------

_GENERIC_LABELS = {
    "cnh": [
        "NOME E SOBRENOME",
        "Nome",
        "CPF",
        "Nº REGISTRO",
        "Registro",
        "VALIDADE",
        "Validade",
        "CAT HAB",
        "Categoria",
        "NASCIMENTO",
        "Nacionalidade",
        "FILIAÇÃO",
        "Filiação",
        "DOC IDENTIDADE",
        "1ª HABILITAÇÃO",
        "DATA EMISSÃO",
    ],
    "cd_sd": [
        "NOME",
        "NOME DA MÃE",
        "CPF",
        "CTPS",
        "DATA ADMISS",
        "DATA DISPENSA",
        "AVISO PRÉVIO",
        "PIS",
    ],
    "identidade": ["Nome", "CPF", "RG", "Nascimento", "Filiação", "Naturalidade"],
    "ctps": ["Nome", "CPF", "PIS", "Nascimento", "Filiação", "CTPS"],
}


def _extract_generic_kv(
    lines: List[Line], kind: str, plain: str
) -> ExtractionResult:
    labels = _GENERIC_LABELS.get(kind, ["Nome", "CPF", "CNPJ"])
    pairs = _label_value_pairs(lines, labels)
    title = {
        "cnh": "# CARTEIRA NACIONAL DE HABILITAÇÃO / DRIVER LICENSE",
        "cd_sd": "# Comunicação de Dispensa e Seguro-Desemprego",
        "identidade": "# Documento de Identificação",
        "ctps": "# Carteira de Trabalho (CTPS)",
    }.get(kind, f"# {kind}")
    parts = [title, ""]
    for key, value in pairs.items():
        parts.append(f"**{key}**: {value}")
    fill = min(1.0, len(pairs) / max(3, min(6, len(labels))))
    return ExtractionResult(
        kind=kind,
        markdown="\n".join(parts).strip(),
        source="template",
        ok=False,
        fill_ratio=fill,
        fields=pairs,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _label_value_pairs(lines: Sequence[Line], labels: Sequence[str]) -> Dict[str, str]:
    """Split visual lines into label→value using known labels (longest first)."""
    ordered = sorted(set(labels), key=len, reverse=True)
    compiled = [(lab, re.compile(rf"(?<!\w){re.escape(lab)}(?!\w)", re.I)) for lab in ordered]
    pairs: Dict[str, str] = {}
    for line in lines:
        text = line.text
        if not text:
            continue
        hits: List[Tuple[int, int, str]] = []
        for lab, cre in compiled:
            for match in cre.finditer(text):
                hits.append((match.start(), match.end(), lab))
        if not hits:
            continue
        hits.sort()
        # Drop overlapping shorter matches
        filtered: List[Tuple[int, int, str]] = []
        for start, end, lab in hits:
            if any(start < fe and end > fs for fs, fe, _ in filtered):
                continue
            filtered.append((start, end, lab))
        for i, (start, end, lab) in enumerate(filtered):
            stop = filtered[i + 1][0] if i + 1 < len(filtered) else len(text)
            value = text[end:stop].strip(" :.-|\t")
            if value and not _looks_like_label_only(value):
                pairs.setdefault(lab, value)
    return _canonicalize_keys(pairs)


def _kv_next_line(lines: Sequence[Line], labels: Sequence[str]) -> Dict[str, str]:
    ordered = sorted(set(labels), key=len, reverse=True)
    pairs: Dict[str, str] = {}
    for i, line in enumerate(lines[:-1]):
        text = line.text.strip(" :")
        for lab in ordered:
            if re.fullmatch(rf"{re.escape(lab)}\s*:?", text, re.I):
                nxt = lines[i + 1].text.strip()
                if nxt and not any(re.fullmatch(rf"{re.escape(x)}\s*:?", nxt, re.I) for x in ordered):
                    pairs.setdefault(lab, nxt)
                break
    return _canonicalize_keys(pairs)


def _canonicalize_keys(pairs: Dict[str, str]) -> Dict[str, str]:
    aliases = {
        "Mae": "Mae",
        "Mãe": "Mae",
        "Cod Município": "Cód Município",
        "Cod Atividade": "Cód Atividade",
        "Titulo Eleitor": "Título Eleitor",
        "Habilitacao": "Habilitação",
        "Admissao": "Admissão",
        "Transferencia": "Transferência",
        "Observacoes": "Observações",
        "Conjuge": "Cônjuge",
        "Gênero": "Genero",
        "Raca": "Raça",
        "Serie": "Série",
        "Emissao": "Emissão",
        "Orgao": "Orgão",
        "Órgão": "Orgão",
        "Secao": "Seção",
        "Mes": "Mês",
        "No Conta FGTS": "Nº Conta FGTS",
        "Cod. Estab.": "Cód. Estab.",
        "Data Admissao": "Data Admissão",
        "Data/Cod. Movimentacao": "Data/Cód. Movimentação",
        "Data Opcao": "Data Opção",
        "Valor Base para Fins Rescisorios": "Valor Base para Fins Rescisórios",
        "Saldo": "SALDO",
        "Nome do Funcionário": "Nome do Funcionário",
    }
    out: Dict[str, str] = {}
    for key, value in pairs.items():
        out[aliases.get(key, key)] = value
    return out


def _first_present(pairs: Dict[str, str], key: str) -> Optional[str]:
    if key in pairs:
        return key
    aliases = {
        "Mae": ["Mãe", "Mae"],
        "Admissão": ["Admissão", "Admissao"],
        "Cód Município": ["Cód Município", "Cod Município"],
        "Cód Atividade": ["Cód Atividade", "Cod Atividade"],
        "Orgão": ["Orgão", "Orgao", "Órgão"],
        "SALDO": ["SALDO", "Saldo"],
        "Nº Conta FGTS": ["Nº Conta FGTS", "No Conta FGTS"],
        "Cód. Estab.": ["Cód. Estab.", "Cod. Estab."],
    }
    for alt in aliases.get(key, [key]):
        if alt in pairs:
            return alt
    return None


def _extract_named_tables(
    lines: Sequence[Line],
    specs: Sequence[Tuple[str, List[str], str]],
) -> List[Tuple[str, str, int]]:
    found: List[Tuple[str, str, int]] = []
    used = set()
    for title, headers, start_re in specs:
        cre = re.compile(start_re, re.I)
        for i, line in enumerate(lines):
            if i in used:
                continue
            if not cre.search(line.text) and not cre.search(_ocr_simplify(line.text)):
                continue
            header_idx = i
            cols = _match_headers_on_line(line, headers)
            if not cols and i + 1 < len(lines):
                cols = _match_headers_on_line(lines[i + 1], headers)
                if cols:
                    header_idx = i + 1
            if not cols:
                continue
            rows: List[List[str]] = []
            for j in range(header_idx + 1, len(lines)):
                nxt = lines[j]
                if any(re.search(s[2], nxt.text, re.I) for s in specs if s[0] != title):
                    break
                if re.search(r"p[aá]gina\s+\d|nydus|documento\s+assinado", nxt.text, re.I):
                    break
                cells = [line_text_in_x_range(nxt, a, b) for _, a, b in cols]
                if not any(cells):
                    if rows:
                        break
                    continue
                rows.append(cells)
                used.add(j)
            used.add(i)
            used.add(header_idx)
            if rows:
                found.append((title, rows_to_html(headers, rows), len(rows)))
            break
    return found


def _match_headers_on_line(
    line: Line, headers: Sequence[str]
) -> Optional[List[Tuple[str, int, int]]]:
    remaining = list(line.words)
    cols: List[Tuple[str, int, int]] = []
    for header in headers:
        tokens = header.split()
        idx = _find_token_sequence(remaining, tokens)
        if idx is None:
            return None
        span = remaining[idx : idx + len(tokens)]
        x1, x2 = span[0].x, span[-1].x2
        cols.append((header, x1, x2))
        remaining = remaining[idx + len(tokens) :]
    # extend each band to the next header
    out: List[Tuple[str, int, int]] = []
    for i, (header, x1, x2) in enumerate(cols):
        x_end = cols[i + 1][1] if i + 1 < len(cols) else (line.x2 + 40)
        out.append((header, x1, max(x_end, x2 + 10)))
    return out


def _find_token_sequence(words: Sequence[Word], tokens: Sequence[str]) -> Optional[int]:
    n, m = len(words), len(tokens)
    if m == 0 or n < m:
        return None
    norm_tokens = [_norm_token(t) for t in tokens]
    for i in range(n - m + 1):
        if all(_norm_token(words[i + k].text) == norm_tokens[k] for k in range(m)):
            return i
    # accent-insensitive / truncated OCR (Referencia vs Referência)
    for i in range(n - m + 1):
        ok = True
        for k in range(m):
            a = _ocr_fold(words[i + k].text)
            b = _ocr_fold(tokens[k])
            if a != b and not (len(a) >= 4 and (a.startswith(b[:4]) or b.startswith(a[:4]))):
                ok = False
                break
        if ok:
            return i
    return None


def _norm_token(text: str) -> str:
    return re.sub(r"[^\w]", "", (text or ""), flags=re.U).lower()


def _fold(text: str) -> str:
    table = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                          "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")
    return _norm_token((text or "").translate(table))


def _ocr_simplify(text: str) -> str:
    """Lowercase, strip accents, keep spaces; fix common ç→g OCR swaps."""
    table = str.maketrans(
        "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
        "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC",
    )
    t = (text or "").translate(table).lower()
    return (
        t.replace("gao", "cao")
        .replace("goes", "coes")
        .replace("gdo", "cao")
        .replace("emorecador", "empregador")
        .replace("empreaador", "empregador")
    )


def _ocr_fold(text: str) -> str:
    """Accent-insensitive token fold plus common Tesseract swaps (ç→g, ã→a)."""
    return _norm_token(_ocr_simplify(text))


def _lines_from_plain(plain: str) -> List[Line]:
    dummy: List[Line] = []
    y = 0
    for raw in (plain or "").splitlines():
        text = raw.strip()
        if not text:
            y += 20
            continue
        dummy.append(
            Line(words=[Word(text=tok, x=i * 40, y=y, w=max(20, len(tok) * 8), h=16)
                        for i, tok in enumerate(text.split())])
        )
        y += 20
    return dummy


def _with_ocr_details(markdown: str, ocr_text: str) -> str:
    body = (markdown or "").rstrip()
    raw = (ocr_text or "").strip()
    if not raw:
        return body
    if "<details>" in body:
        return body
    return (
        f"{body}\n\n<details><summary>Texto OCR completo</summary>\n\n"
        f"{raw}\n\n</details>"
    )


def _looks_like_label_only(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _is_numbered_label_text(t) and not _MONEY_RE.search(t) and not _DATE_RE.search(t):
        return True
    return False


def _is_numbered_label_text(text: str) -> bool:
    return bool(re.match(r"^\d{1,3}(?:[.,]\d)?\s+[A-Za-zÀ-ú(]", text or ""))


def _num_key(num: str) -> float:
    try:
        return float(str(num).replace(",", "."))
    except ValueError:
        return 0.0


def _num_key_str(num: str) -> str:
    token = str(num).replace(",", ".")
    if re.match(r"^\d+\.0$", token):
        token = token.split(".")[0]
    if re.match(r"^\d+$", token):
        return token.zfill(2) if len(token) <= 2 else token
    return token


def _search_money(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", re.I)
    return match.group(1) if match else ""


def _search_line_after(text: str, pattern: str) -> str:
    match = re.search(rf"{pattern}\s*[:|]?\s*(.+)", text or "", re.I)
    return match.group(1).strip() if match else ""
