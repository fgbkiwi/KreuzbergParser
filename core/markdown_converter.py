"""
Markdown Converter - converts OCR results to structured markdown
"""
from typing import List, Dict
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MarkdownConverter:
    """Converts OCR results to formatted Markdown"""
    
    def __init__(self, config=None):
        from config import Config
        self.config = config or Config()
    
    def convert_to_markdown(
        self,
        result_data: Dict,
        output_path: str
    ) -> str:
        """
        Convert OCR results to Markdown file
        
        Args:
            result_data: Results from KreuzbergOCREngine
            output_path: Path to save markdown file
        
        Returns:
            Path to created markdown file
        """
        output_path = Path(output_path)
        
        # Build markdown content
        md_content = self._build_markdown_content(result_data)
        
        # Save file
        output_path.write_text(md_content, encoding=self.config.MD_ENCODING)
        
        logger.info(f"Markdown saved: {output_path}")
        return str(output_path)
    
    def _build_markdown_content(self, result_data: Dict) -> str:
        """Build complete markdown content"""
        lines = []
        
        pages = result_data.get('pages', [])
        stats = result_data.get('statistics', {})
        metadata = result_data.get('metadata', {})
        
        # Title
        filename = metadata.get('filename', 'Documento')
        lines.append(f"# 📄 Documento: {filename}")
        lines.append("")
        
        # Metadata section
        if self.config.INCLUDE_METADATA:
            lines.extend(self._build_metadata_section(metadata, stats))
            lines.append("")
        
        # Pages
        for page_data in pages:
            lines.extend(self._build_page_section(page_data))
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # Statistics
        if self.config.INCLUDE_STATISTICS:
            lines.extend(self._build_statistics_section(stats))
        
        return "\n".join(lines)
    
    def _build_metadata_section(self, metadata: Dict, stats: Dict) -> List[str]:
        """Build metadata section"""
        lines = ["## 📋 Metadados do Documento", ""]
        
        total_pages = metadata.get('total_pages', 0)
        lines.append(f"- **Total de páginas**: {total_pages}")
        
        mode = metadata.get('mode', 'N/A')
        backend = metadata.get('backend', 'N/A')
        lines.append(f"- **Modo de processamento**: {mode}")
        lines.append(f"- **Engine OCR**: {backend}")
        
        processing_time = metadata.get('processing_time', 0)
        lines.append(f"- **Tempo total de processamento**: {processing_time:.2f}s")
        
        if self.config.INCLUDE_TIMESTAMPS:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"- **Data de processamento**: {now}")
        
        # Type breakdown
        native_count = stats.get('native_pages', 0)
        scanned_count = stats.get('scanned_pages', 0)
        
        lines.append("")
        lines.append("### 📊 Análise de Páginas")
        lines.append(f"- **Páginas nativas** (texto digital): {native_count}")
        lines.append(f"- **Páginas escaneadas** (OCR aplicado): {scanned_count}")
        
        pages_with_tables = stats.get('pages_with_tables', 0)
        if pages_with_tables > 0:
            lines.append(f"- **Páginas com tabelas**: {pages_with_tables}")
        
        total_words = stats.get('total_words', 0)
        total_chars = stats.get('total_characters', 0)
        lines.append(f"- **Total de palavras extraídas**: {total_words:,}")
        lines.append(f"- **Total de caracteres**: {total_chars:,}")
        
        return lines
    
    def _build_page_section(self, page_data: Dict) -> List[str]:
        """Build section for a single page"""
        page_num = page_data.get('page', 0)
        lines = [f"## 📄 Página {page_num}", ""]
        
        # Text content
        text = page_data.get('text', '').strip()
        if text:
            lines.append("### 📝 Conteúdo Extraído")
            lines.append("")
            lines.append(text)
            lines.append("")
        else:
            lines.append("*Nenhum texto extraído desta página*")
            lines.append("")
        
        # Processing info
        if self.config.INCLUDE_PROCESSING_INFO:
            lines.extend(self._build_page_info(page_data))
        
        return lines
    
    def _build_page_info(self, page_data: Dict) -> List[str]:
        """Build processing information for page"""
        lines = ["### ℹ️ Informações de Processamento", ""]
        
        page_type = page_data.get('type', 'unknown')
        type_display = {
            'native': 'Nativa (texto digital)',
            'scanned': 'Escaneada (OCR)',
            'unknown': 'Desconhecido'
        }.get(page_type, page_type)
        
        lines.append(f"- **Tipo**: {type_display}")
        
        # Tables
        if page_data.get('has_tables', False):
            lines.append("- **Tabelas detectadas**: Sim ✓")
        
        # Language
        language = page_data.get('language')
        if language:
            lines.append(f"- **Idioma detectado**: {language}")
        
        # Handwriting
        if page_data.get('handwriting_detected', False):
            confidence = page_data.get('handwriting_confidence', 0)
            lines.append(f"- **Texto manuscrito detectado**: Sim (confiança: {confidence:.1%})")
        
        # Word count
        word_count = page_data.get('word_count', 0)
        char_count = page_data.get('char_count', 0)
        lines.append(f"- **Estatísticas**: {word_count} palavras, {char_count} caracteres")
        
        lines.append("")
        return lines
    
    def _build_statistics_section(self, stats: Dict) -> List[str]:
        """Build final statistics section"""
        lines = ["## 📊 Estatísticas Gerais do Processamento", ""]
        
        total_pages = stats.get('total_pages', 0)
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time_per_page', 0)
        speed = stats.get('processing_speed', 'N/A')
        
        lines.append("### ⏱️ Performance")
        lines.append(f"- **Tempo total**: {total_time:.2f}s")
        lines.append(f"- **Tempo médio por página**: {avg_time:.2f}s")
        lines.append(f"- **Velocidade de processamento**: {speed}")
        lines.append("")
        
        lines.append("### 📈 Conteúdo Extraído")
        lines.append(f"- **Total de páginas processadas**: {total_pages}")
        
        native_pages = stats.get('native_pages', 0)
        scanned_pages = stats.get('scanned_pages', 0)
        lines.append(f"- **Páginas nativas**: {native_pages}")
        lines.append(f"- **Páginas com OCR**: {scanned_pages}")
        
        pages_with_tables = stats.get('pages_with_tables', 0)
        lines.append(f"- **Páginas com tabelas**: {pages_with_tables}")
        
        total_words = stats.get('total_words', 0)
        total_chars = stats.get('total_characters', 0)
        lines.append(f"- **Total de palavras**: {total_words:,}")
        lines.append(f"- **Total de caracteres**: {total_chars:,}")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*Documento processado com Sistema Inteligente de OCR powered by Kreuzberg*")
        
        return lines
