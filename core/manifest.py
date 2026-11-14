"""Manifesto dos autos: índice JSON, página a página, do documento do PJe a que
cada página pertence (ID, tipo e título do SUMÁRIO).

Gravado ao lado do Markdown (`<nome>.manifesto.json`) para que outros programas
saibam onde começam e terminam a petição inicial, a contestação, as atas, os
laudos e os demais documentos, sem reprocessar o PDF.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from core.pje_sumario import SumarioEntry, _ROW_RE, resolve_entry_for_page

MANIFEST_VERSION = 1
# Uma página com pelo menos este número de linhas de sumário é o próprio SUMÁRIO.
_MIN_SUMARIO_ROWS = 3


def _is_sumario_page(text: str) -> bool:
    return len(_ROW_RE.findall(text or "")) >= _MIN_SUMARIO_ROWS


def build_manifest(result: Dict, sumario: Optional[Dict[str, SumarioEntry]] = None) -> Dict:
    """Monta o manifesto a partir do resultado de `KreuzbergOCREngine.process_pdf`."""
    sumario = sumario or {}
    metadata = result.get("metadata", {})
    paginas: List[Dict] = []
    for page in result.get("pages", []):
        signature_ids = list(page.get("signature_ids") or [])
        entry = resolve_entry_for_page(signature_ids, sumario) if sumario else None
        doc_id = entry.doc_id if entry else (signature_ids[-1] if signature_ids else None)
        paginas.append({
            "pagina": page.get("page"),
            "fls": page.get("fls"),
            "doc_id": doc_id.lower() if doc_id else None,
            "tipo": (entry.tipo if entry else page.get("sumario_tipo")) or None,
            "titulo": (entry.titulo if entry else page.get("sumario_titulo")) or None,
            "data": entry.data_assinatura if entry else None,
            "classe": page.get("type"),
            "ocr_falhou": bool(page.get("ocr_failed")),
            "sumario": _is_sumario_page(page.get("text") or ""),
        })
    return {
        "versao": MANIFEST_VERSION,
        "arquivo": metadata.get("filename"),
        "processo": metadata.get("processo"),
        "total_paginas": len(paginas),
        "documentos_no_sumario": len(sumario),
        "paginas": paginas,
    }


def manifest_path_for(markdown_path: str | Path) -> Path:
    return Path(markdown_path).with_suffix(".manifesto.json")


def write_manifest(
    result: Dict,
    markdown_path: str | Path,
    sumario: Optional[Dict[str, SumarioEntry]] = None,
) -> Path:
    path = manifest_path_for(markdown_path)
    path.write_text(
        json.dumps(build_manifest(result, sumario), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path
