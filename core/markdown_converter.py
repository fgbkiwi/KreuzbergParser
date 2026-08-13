"""
Markdown Converter - converts OCR results to clean structured markdown.

Page body contains only the converted text. Signature lines are kept
only on the last page of each signature-ID group.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_FLS_LINE_RE = re.compile(
    r"(?mi)^\s*Fls[._]?\s*:?\s*\d+\s*$"
)
_SIGNATURE_LINE_RE = re.compile(
    r"(?mi)^\s*Documento assinado eletronicamente por .+?$"
)
_SIGNATURE_ID_RE = re.compile(
    r"(?mi)Documento assinado eletronicamente por .+?\s*-\s*([0-9A-Za-z]{7})\b"
)
_PJE_URL_LINE_RE = re.compile(
    r"(?mi)^\s*https?://[^\s]*pje[^\s]*\.jus\.br\S*\s*$"
)
_DOC_NUM_LINE_RE = re.compile(
    r"(?mi)^\s*N[uú]mero do documento\s*:.*$"
)
_PROC_NUM_LINE_RE = re.compile(
    r"(?mi)^\s*N[uú]mero do processo\s*:.*$"
)


class MarkdownConverter:
    """Converts OCR results to formatted Markdown"""

    def __init__(self, config=None):
        from config import Config

        self.config = config or Config()

    def convert_to_markdown(self, result_data: Dict, output_path: str) -> str:
        output_path = Path(output_path)
        md_content = self._build_markdown_content(result_data, output_path.parent)
        output_path.write_text(md_content, encoding=self.config.MD_ENCODING)
        logger.info("Markdown saved: %s", output_path)
        return str(output_path)

    def _build_markdown_content(
        self, result_data: Dict, md_parent: Path
    ) -> str:
        lines: List[str] = []

        pages = list(result_data.get("pages", []))
        stats = result_data.get("statistics", {})
        metadata = result_data.get("metadata", {})

        filename = metadata.get("filename", "Documento")
        processo = metadata.get("processo")
        lines.append(f"# Documento: {filename}")
        lines.append("")
        if processo:
            lines.append(f"**Número do processo**: `{processo}`")
            lines.append("")

        if self.config.INCLUDE_METADATA:
            lines.extend(self._build_metadata_section(metadata, stats))
            lines.append("")

        keep_signature_flags = self._compute_keep_signature_flags(pages)

        for idx, page_data in enumerate(pages):
            keep_sig = keep_signature_flags[idx] if idx < len(keep_signature_flags) else False
            lines.extend(self._build_page_section(page_data, keep_signature=keep_sig))
            lines.append("")
            lines.append("---")
            lines.append("")

        if self.config.INCLUDE_STATISTICS:
            lines.extend(self._build_statistics_section(stats))

        return "\n".join(lines)

    def _compute_keep_signature_flags(self, pages: Sequence[Dict]) -> List[bool]:
        """
        Keep PJe signature lines only on the last page of each signature-ID group.

        Group key = frozenset of signature IDs on the page. When the next page
        has a different ID set, the current page is the last of its group.
        """
        n = len(pages)
        flags = [False] * n
        for i, page in enumerate(pages):
            ids = self._page_signature_ids(page)
            if not ids:
                continue
            next_ids: Set[str] = set()
            if i + 1 < n:
                next_ids = self._page_signature_ids(pages[i + 1])
            # Last page of group when next page has different ID set
            if ids != next_ids:
                flags[i] = True
        return flags

    def _page_signature_ids(self, page: Dict) -> Set[str]:
        ids = page.get("signature_ids") or []
        result = {str(i).lower() for i in ids if i}
        if result:
            return result
        blob = "\n".join(
            [
                page.get("signature") or "",
                page.get("pje_stamp") or "",
                "\n".join(page.get("signature_lines") or []),
                page.get("text") or "",
            ]
        )
        for match in _SIGNATURE_ID_RE.finditer(blob):
            result.add(match.group(1).lower())
        return result

    def _build_metadata_section(self, metadata: Dict, stats: Dict) -> List[str]:
        lines = ["## Metadados do Documento", ""]

        lines.append(f"- **Total de páginas**: {metadata.get('total_pages', 0)}")
        lines.append(f"- **Modo de processamento**: {metadata.get('mode', 'N/A')}")
        lines.append(f"- **Engine OCR**: {metadata.get('backend', 'N/A')}")

        processing_time = metadata.get("processing_time", 0) or 0
        lines.append(f"- **Tempo total de processamento**: {processing_time:.2f}s")

        if metadata.get("log_path"):
            lines.append(f"- **Log de auditoria**: `{metadata['log_path']}`")

        if metadata.get("sumario_docs"):
            lines.append(
                f"- **Documentos no SUMÁRIO PJe**: {metadata['sumario_docs']}"
            )

        if self.config.INCLUDE_TIMESTAMPS:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"- **Data de processamento**: {now}")

        lines.append("")
        lines.append("### Análise de Páginas")
        lines.append(
            f"- **Páginas nativas** (texto digital): {stats.get('native_pages', 0)}"
        )
        lines.append(f"- **Páginas híbridas**: {stats.get('hybrid_pages', 0)}")
        lines.append(
            f"- **Páginas imagem/OCR**: {stats.get('image_pages', stats.get('scanned_pages', 0))}"
        )
        if stats.get("ocr_failed_pages"):
            lines.append(f"- **Falhas de OCR**: {stats['ocr_failed_pages']}")

        pages_with_tables = stats.get("pages_with_tables", 0)
        if pages_with_tables > 0:
            lines.append(f"- **Páginas com tabelas**: {pages_with_tables}")

        lines.append(
            f"- **Total de palavras extraídas**: {stats.get('total_words', 0):,}"
        )
        lines.append(
            f"- **Total de caracteres**: {stats.get('total_characters', 0):,}"
        )
        return lines

    def _build_page_section(
        self, page_data: Dict, *, keep_signature: bool
    ) -> List[str]:
        page_num = page_data.get("page", 0)
        fls = page_data.get("fls") or page_data.get("marker_page_number")

        title = f"## Página {page_num}"
        if fls is not None:
            title += f" — Fls.: {fls}"
        lines = [title, ""]

        if page_data.get("ocr_failed"):
            reason = page_data.get("ocr_failure_reason") or "ocr_failed"
            lines.append(f"> **OCR falhou**: `{reason}`")
            lines.append("")

        text = self._clean_page_body(
            page_data.get("text") or "",
            keep_signature=keep_signature,
            signature_lines=page_data.get("signature_lines") or [],
        )

        if text:
            lines.append(text)
            lines.append("")
        elif page_data.get("ocr_failed"):
            lines.append("*Nenhum texto utilizável após falha de OCR.*")
            lines.append("")
        else:
            lines.append("*Nenhum texto extraído desta página*")
            lines.append("")

        return lines

    def _clean_page_body(
        self,
        text: str,
        *,
        keep_signature: bool,
        signature_lines: Sequence[str],
    ) -> str:
        """Remove duplicate Fls. lines and manage PJe signature lines."""
        if not text:
            # Still may want signatures alone on last page
            if keep_signature and signature_lines:
                return "\n".join(signature_lines)
            return ""

        out_lines: List[str] = []
        for line in text.splitlines():
            if _FLS_LINE_RE.match(line):
                continue
            if _SIGNATURE_LINE_RE.match(line):
                # Drop from body; re-append at end only if keep_signature
                continue
            if _PJE_URL_LINE_RE.match(line):
                continue
            if _DOC_NUM_LINE_RE.match(line):
                continue
            if _PROC_NUM_LINE_RE.match(line):
                continue
            out_lines.append(line)

        body = "\n".join(out_lines)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()

        if keep_signature:
            sigs = list(signature_lines)
            if not sigs:
                # Recover from original text
                for line in text.splitlines():
                    if _SIGNATURE_LINE_RE.match(line):
                        sigs.append(line.strip())
            if sigs:
                if body:
                    body = body + "\n\n" + "\n".join(sigs)
                else:
                    body = "\n".join(sigs)

        return body.strip()

    def _build_statistics_section(self, stats: Dict) -> List[str]:
        lines = ["## Estatísticas Gerais do Processamento", ""]

        total_pages = stats.get("total_pages", 0)
        total_time = stats.get("total_time", 0)
        avg_time = stats.get("avg_time_per_page", 0)
        speed = stats.get("processing_speed", "N/A")

        lines.append("### Performance")
        lines.append(f"- **Tempo total**: {total_time:.2f}s")
        lines.append(f"- **Tempo médio por página**: {avg_time:.2f}s")
        lines.append(f"- **Velocidade de processamento**: {speed}")
        lines.append("")

        lines.append("### Conteúdo")
        lines.append(f"- **Total de páginas processadas**: {total_pages}")
        lines.append(f"- **Páginas nativas**: {stats.get('native_pages', 0)}")
        lines.append(f"- **Páginas híbridas**: {stats.get('hybrid_pages', 0)}")
        lines.append(
            f"- **Páginas com OCR**: "
            f"{stats.get('image_pages', stats.get('scanned_pages', 0))}"
        )
        lines.append(
            f"- **Páginas com tabelas**: {stats.get('pages_with_tables', 0)}"
        )
        lines.append(f"- **Total de palavras**: {stats.get('total_words', 0):,}")
        lines.append(
            f"- **Total de caracteres**: {stats.get('total_characters', 0):,}"
        )

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(
            "*Documento processado com Sistema Inteligente de OCR "
            "powered by Kreuzberg*"
        )
        return lines
