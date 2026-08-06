"""
Kreuzberg-powered OCR Engine
Replaces 1000+ lines of custom code with Kreuzberg integration
"""
import logging
import re
from typing import List, Dict, Optional
from pathlib import Path
import time

try:
    import kreuzberg
    KREUZBERG_AVAILABLE = True
except ImportError:
    KREUZBERG_AVAILABLE = False
    logging.warning("Kreuzberg not installed. Install with: pip install kreuzberg")

from config import Config, ProcessingMode
from utils.gpu_detector import gpu_detector
from utils.tessdata import ensure_tessdata

logger = logging.getLogger(__name__)


class KreuzbergOCREngine:
    """
    Simplified OCR engine using Kreuzberg
    Replaces: PageAnalyzer + ImagePreprocessor + OCRPipeline (1000+ lines)
    """
    
    def __init__(self, mode: ProcessingMode, config: Config = None):
        """
        Initialize Kreuzberg OCR engine
        
        Args:
            mode: Processing mode (GPU/CPU/EXPRESS)
            config: Configuration object
        """
        if not KREUZBERG_AVAILABLE:
            raise ImportError("Kreuzberg library not installed. Run: pip install kreuzberg")
        
        self.mode = mode
        self.config = config or Config()
        self.mode_config = self.config.get_mode_config(mode)
        
        # GPU validation for GPU mode
        if mode == ProcessingMode.GPU:
            is_suitable, message = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
            if not is_suitable:
                logger.warning(f"GPU inadequate: {message}. Falling back to CPU mode.")
                self.mode = ProcessingMode.CPU
                self.mode_config = self.config.get_mode_config(ProcessingMode.CPU)
        
        logger.info(f"Kreuzberg OCR Engine initialized in {self.mode} mode")
        logger.info(f"Backend: {self.mode_config.get('backend', 'tesseract')}")

        if self.mode_config.get('backend', 'tesseract') == 'tesseract':
            try:
                ensure_tessdata(self.config.TESSDATA_DIR, self.config.TESSERACT_LANGUAGES)
            except Exception as exc:
                logger.warning("Tessdata setup failed: %s", exc)
    
    def process_pdf(self, pdf_path: str) -> Dict:
        """
        Process entire PDF with Kreuzberg
        
        Args:
            pdf_path: Path to PDF file
        
        Returns:
            Dict with processed results and statistics
        """
        start_time = time.time()
        pdf_path = Path(pdf_path)
        
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        
        logger.info(f"Processing PDF: {pdf_path.name}")
        
        try:
            # Configure Kreuzberg extraction
            extraction_config, easyocr_kwargs = self._build_extraction_config()
            
            # Extract using Kreuzberg (replaces all custom extraction code)
            try:
                result = self._extract_with_kreuzberg(pdf_path, extraction_config, easyocr_kwargs)
            except Exception as exc:
                error_text = str(exc)
                if self.mode == ProcessingMode.GPU and "easyocr" in error_text.lower():
                    logger.warning(
                        "GPU OCR failed (%s). Falling back to CPU mode for OCR.",
                        error_text,
                    )
                    self.mode = ProcessingMode.CPU
                    self.mode_config = self.config.get_mode_config(ProcessingMode.CPU)
                    extraction_config, easyocr_kwargs = self._build_extraction_config()
                    result = self._extract_with_kreuzberg(pdf_path, extraction_config, easyocr_kwargs)
                else:
                    raise
            
            # Convert to our format
            pages_data = self._convert_kreuzberg_result(result)
            
            # Statistics
            total_time = time.time() - start_time
            stats = self._calculate_statistics(pages_data, total_time)
            
            return {
                'pages': pages_data,
                'statistics': stats,
                'metadata': {
                    'filename': pdf_path.name,
                    'total_pages': stats.get('total_pages', len(pages_data)),
                    'mode': self.mode,
                    'backend': self.mode_config.get('backend'),
                    'processing_time': total_time
                }
            }
            
        except Exception as e:
            logger.exception(f"Error processing PDF: {e}")
            raise
    
    def _extract_with_kreuzberg(self, pdf_path: Path, extraction_config, easyocr_kwargs):
        """Call Kreuzberg extract_file_sync with the current API surface."""
        kwargs = {
            "config": extraction_config,
        }
        if easyocr_kwargs is not None:
            kwargs["easyocr_kwargs"] = easyocr_kwargs
        return kreuzberg.extract_file_sync(str(pdf_path), **kwargs)

    def _build_extraction_config(self):
        """Build Kreuzberg extraction configuration"""
        language = self.mode_config.get('language', 'por')
        backend = self.mode_config.get('backend', 'tesseract')

        language = self.config.BACKEND_LANGUAGE_MAP.get(backend, {}).get(language, language)
        logger.info(f"OCR language for {backend}: {language}")

        easyocr_kwargs = None
        use_gpu = bool(self.mode_config.get('use_gpu', False))

        ocr_config = None
        if backend == 'tesseract':
            tesseract_config = kreuzberg.TesseractConfig(
                language=language,
                enable_table_detection=self.mode_config.get('detect_tables', True),
            )
            ocr_config = kreuzberg.OcrConfig(
                backend='tesseract',
                language=language,
                tesseract_config=tesseract_config,
            )
        elif backend == 'paddleocr':
            ocr_config = kreuzberg.OcrConfig(
                backend='paddleocr',
                language=language,
                paddle_ocr_config=kreuzberg.PaddleOcrConfig(
                    language=language,
                    enable_table_detection=self.mode_config.get('detect_tables', True),
                ),
            )
        elif backend == 'easyocr':
            ocr_config = kreuzberg.OcrConfig(backend='easyocr', language=language)
            if use_gpu:
                easyocr_kwargs = {"use_gpu": True}

        language_detection = kreuzberg.LanguageDetectionConfig(
            enabled=self.config.KREUZBERG_LANGUAGE_DETECTION
        )
        images = kreuzberg.ImageExtractionConfig(
            extract_images=self.config.KREUZBERG_EXTRACT_IMAGES,
            target_dpi=self.mode_config.get('dpi', 300),
        )
        pdf_options = kreuzberg.PdfConfig(
            extract_images=self.config.KREUZBERG_EXTRACT_IMAGES
        )

        acceleration = None
        if use_gpu:
            acceleration = kreuzberg.AccelerationConfig(provider="cuda")

        config = kreuzberg.ExtractionConfig(
            ocr=ocr_config,
            language_detection=language_detection,
            images=images,
            pdf_options=pdf_options,
            force_ocr=self.mode_config.get('force_ocr', False),
            acceleration=acceleration,
        )

        logger.debug("Kreuzberg config built")
        return config, easyocr_kwargs
    
    def _convert_kreuzberg_result(self, result) -> List[Dict]:
        """
        Convert Kreuzberg result to our page format
        
        Args:
            result: Kreuzberg extraction result
        
        Returns:
            List of page dictionaries
        """
        pages = []

        try:
            kreuzberg_pages = self._extract_kreuzberg_pages(result)
            for idx, page_result in enumerate(kreuzberg_pages):
                page_data = self._convert_page(page_result, idx + 1)
                pages.append(page_data)

            pages = self._normalize_page_sequence(pages)

        except Exception as e:
            logger.error(f"Error converting Kreuzberg result: {e}")
            pages.append({
                'page': 1,
                'text': str(result),
                'type': 'unknown',
                'has_tables': False,
                'language': None,
                'processing_time': 0
            })

        return pages

    def _extract_kreuzberg_pages(self, result) -> List:
        """Extract pages from different Kreuzberg result shapes."""
        if hasattr(result, 'pages') and result.pages:
            return list(result.pages)

        if hasattr(result, 'documents') and result.documents:
            pages = []
            for doc in result.documents:
                if hasattr(doc, 'pages') and doc.pages:
                    pages.extend(list(doc.pages))
                elif hasattr(doc, 'content') and doc.content:
                    pages.extend(self._normalize_content_pages(doc.content))
            if pages:
                return pages

        if hasattr(result, 'content') and result.content:
            if isinstance(result.content, list):
                return self._normalize_content_pages(result.content)
            if isinstance(result.content, str):
                return self._split_text_pages(result.content)
            return [{'text': str(result.content), 'page_number': 1}]

        return [{'text': str(result), 'page_number': 1}]

    def _normalize_content_pages(self, content) -> List:
        """Normalize content into a list of page-like objects."""
        if isinstance(content, list):
            if not content:
                return []
            if isinstance(content[0], dict):
                return content
            if isinstance(content[0], str):
                return [{'text': text, 'page_number': idx + 1} for idx, text in enumerate(content)]
            return [{'text': str(item), 'page_number': idx + 1} for idx, item in enumerate(content)]

        if isinstance(content, str):
            return self._split_text_pages(content)

        return [{'text': str(content), 'page_number': 1}]

    def _split_text_pages(self, text: str) -> List[Dict]:
        """Split plain text into pages using form feeds or page markers."""
        if "\f" in text:
            parts = text.split("\f")
            return [{'text': part, 'page_number': idx + 1} for idx, part in enumerate(parts)]

        pages = self._split_text_by_page_markers(text)
        if pages:
            return pages

        return [{'text': text, 'page_number': 1}]

    def _split_text_by_page_markers(self, text: str) -> List[Dict]:
        """Split text by common page markers like 'Fls.: 1'."""
        sanitized = self._normalize_marker_text(text)
        marker_regex = self._page_marker_regex()
        matches = list(marker_regex.finditer(sanitized))
        if len(matches) < 2:
            return []

        header_mode = matches[0].start() <= 200
        pages = []
        preamble = sanitized[:matches[0].start()].strip()

        if header_mode:
            for idx, match in enumerate(matches):
                start = match.start()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(sanitized)
                chunk = sanitized[start:end].strip()
                if not chunk:
                    continue

                page_number = self._safe_int(match.group(1))
                if not pages and preamble:
                    chunk = f"{preamble}\n\n{chunk}"

                pages.append({
                    'text': chunk,
                    'page_number': page_number or (len(pages) + 1),
                    'marker_page_number': page_number
                })
        else:
            start = 0
            for idx, match in enumerate(matches):
                end = match.end()
                chunk = sanitized[start:end].strip()
                if chunk:
                    page_number = self._safe_int(match.group(1))
                    pages.append({
                        'text': chunk,
                        'page_number': page_number or (len(pages) + 1),
                        'marker_page_number': page_number
                    })
                start = end

            tail = sanitized[start:].strip()
            if tail:
                pages.append({
                    'text': tail,
                    'page_number': len(pages) + 1,
                    'marker_page_number': None
                })

        return pages
    
    def _convert_page(self, page_result, page_num: int) -> Dict:
        """Convert single Kreuzberg page to our format"""
        try:
            # Extract text
            if isinstance(page_result, dict):
                text = page_result.get('text', '')
            else:
                text = getattr(page_result, 'text', str(page_result))

            marker_number = None
            if isinstance(page_result, dict):
                marker_number = page_result.get('page_number')
            if marker_number is None:
                marker_number = getattr(page_result, 'page_number', None)
            if marker_number is None:
                marker_number = self._extract_page_marker(text)
            
            # Determine page type
            is_native = self._resolve_is_native(page_result, text)
            page_type = 'native' if is_native else 'scanned'
            
            # Tables
            has_tables = False
            if hasattr(page_result, 'tables') and page_result.tables:
                has_tables = len(page_result.tables) > 0
            
            # Language
            language = getattr(page_result, 'language', None)
            
            return {
                'page': page_num,
                'marker_page_number': marker_number,
                'text': text,
                'type': page_type,
                'has_tables': has_tables,
                'language': language,
                'word_count': len(text.split()) if text else 0,
                'char_count': len(text) if text else 0
            }
            
        except Exception as e:
            logger.error(f"Error converting page {page_num}: {e}")
            return {
                'page': page_num,
                'text': str(page_result),
                'type': 'unknown',
                'has_tables': False,
                'language': None,
                'word_count': 0,
                'char_count': 0
            }
    
    def _calculate_statistics(self, pages: List[Dict], total_time: float) -> Dict:
        """Calculate processing statistics"""
        total_pages = len(pages)
        native_count = sum(1 for p in pages if p.get('type') == 'native')
        scanned_count = sum(1 for p in pages if p.get('type') == 'scanned')
        pages_with_tables = sum(1 for p in pages if p.get('has_tables', False))
        
        total_chars = sum(p.get('char_count', 0) for p in pages)
        total_words = sum(p.get('word_count', 0) for p in pages)
        
        return {
            'total_pages': total_pages,
            'native_pages': native_count,
            'scanned_pages': scanned_count,
            'pages_with_tables': pages_with_tables,
            'total_characters': total_chars,
            'total_words': total_words,
            'total_time': round(total_time, 2),
            'avg_time_per_page': round(total_time / total_pages, 2) if total_pages > 0 else 0,
            'processing_speed': f"{total_pages / total_time:.2f} pages/sec" if total_time > 0 else "N/A"
        }

    def _normalize_page_sequence(self, pages: List[Dict]) -> List[Dict]:
        """Normalize page numbers, handle marker resets, and merge duplicates."""
        normalized = []
        last_number = 0

        for idx, page in enumerate(pages, start=1):
            marker_number = page.get('marker_page_number')

            if isinstance(marker_number, int) and marker_number > 0:
                if last_number and marker_number < last_number:
                    marker_number = None
            else:
                marker_number = None

            if marker_number is None:
                assigned = last_number + 1 if last_number else idx
            else:
                assigned = marker_number

            page['page'] = assigned
            last_number = assigned

            if normalized and normalized[-1]['page'] == assigned:
                normalized[-1] = self._merge_pages(normalized[-1], page)
            else:
                normalized.append(page)

        return normalized

    def _merge_pages(self, base: Dict, extra: Dict) -> Dict:
        """Merge two page entries with the same resolved page number."""
        base_text = base.get('text', '')
        extra_text = extra.get('text', '')
        if extra_text:
            combined = f"{base_text}\n\n{extra_text}" if base_text else extra_text
        else:
            combined = base_text

        base['text'] = combined
        base['word_count'] = len(combined.split()) if combined else 0
        base['char_count'] = len(combined) if combined else 0
        base['has_tables'] = base.get('has_tables', False) or extra.get('has_tables', False)

        if base.get('type') != 'scanned' and extra.get('type') == 'scanned':
            base['type'] = 'scanned'

        if not base.get('language'):
            base['language'] = extra.get('language')

        return base

    def _resolve_is_native(self, page_result, text: str) -> bool:
        """Resolve whether a page is native or OCRed."""
        if isinstance(page_result, dict) and 'is_native' in page_result:
            return bool(page_result.get('is_native'))

        attr_value = getattr(page_result, 'is_native', None)
        if attr_value is not None:
            return bool(attr_value)

        return bool(text.strip())

    def _extract_page_marker(self, text: str) -> Optional[int]:
        """Extract page marker number from text if present."""
        sanitized = self._normalize_marker_text(text)
        marker_regex = self._page_marker_regex()
        matches = list(marker_regex.finditer(sanitized))
        if not matches:
            return None

        if matches[0].start() <= 200:
            return self._safe_int(matches[0].group(1))

        if matches[-1].start() >= max(len(sanitized) - 200, 0):
            return self._safe_int(matches[-1].group(1))

        return self._safe_int(matches[0].group(1))

    def _page_marker_regex(self) -> re.Pattern:
        return re.compile(r"(?mi)^\s*Fls\.?\s*:\s*(\d+)\b")

    def _normalize_marker_text(self, text: str) -> str:
        return text.replace("\u00a0", " ")

    def _safe_int(self, value) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None
