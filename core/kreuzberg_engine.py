"""
Kreuzberg-powered OCR Engine
Replaces 1000+ lines of custom code with Kreuzberg integration
"""
import logging
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
            extraction_config = self._build_extraction_config()
            
            # Extract using Kreuzberg (replaces all custom extraction code)
            result = kreuzberg.extract_from_file(
                str(pdf_path),
                config=extraction_config
            )
            
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
                    'total_pages': len(pages_data),
                    'mode': self.mode,
                    'backend': self.mode_config.get('backend'),
                    'processing_time': total_time
                }
            }
            
        except Exception as e:
            logger.exception(f"Error processing PDF: {e}")
            raise
    
    def _build_extraction_config(self) -> dict:
        """Build Kreuzberg extraction configuration"""
        config = {
            'language': self.mode_config.get('language', 'por'),
            'detect_tables': self.mode_config.get('detect_tables', True),
            'extract_images': self.config.KREUZBERG_EXTRACT_IMAGES,
            'detect_language': self.config.KREUZBERG_LANGUAGE_DETECTION,
            'dpi': self.mode_config.get('dpi', 300),
        }
        
        # OCR backend configuration
        backend = self.mode_config.get('backend', 'tesseract')
        if backend == 'tesseract':
            config['ocr_backend'] = 'tesseract'
            config['tesseract_lang'] = 'por'
        elif backend == 'paddleocr' and self.mode_config.get('use_gpu', False):
            config['ocr_backend'] = 'paddleocr'
            config['use_gpu'] = True
        
        # Express mode optimizations
        if self.mode == ProcessingMode.EXPRESS:
            config['skip_preprocessing'] = True
            config['detect_tables'] = False
        
        logger.debug(f"Kreuzberg config: {config}")
        return config
    
    def _convert_kreuzberg_result(self, result) -> List[Dict]:
        """
        Convert Kreuzberg result to our page format
        
        Args:
            result: Kreuzberg extraction result
        
        Returns:
            List of page dictionaries
        """
        pages = []
        
        # Access Kreuzberg result attributes
        try:
            # Handle different Kreuzberg result structures
            if hasattr(result, 'pages'):
                kreuzberg_pages = result.pages
            elif hasattr(result, 'text'):
                # Single page or simple extraction
                kreuzberg_pages = [result]
            else:
                # Fallback: treat as single text result
                kreuzberg_pages = [{'text': str(result), 'page_number': 1}]
            
            for idx, page_result in enumerate(kreuzberg_pages):
                page_data = self._convert_page(page_result, idx + 1)
                pages.append(page_data)
                
        except Exception as e:
            logger.error(f"Error converting Kreuzberg result: {e}")
            # Fallback: single page with full text
            pages.append({
                'page': 1,
                'text': str(result),
                'type': 'unknown',
                'has_tables': False,
                'language': None,
                'processing_time': 0
            })
        
        return pages
    
    def _convert_page(self, page_result, page_num: int) -> Dict:
        """Convert single Kreuzberg page to our format"""
        try:
            # Extract text
            text = getattr(page_result, 'text', str(page_result))
            
            # Determine page type
            is_native = getattr(page_result, 'is_native', len(text) > 50)
            page_type = 'native' if is_native else 'scanned'
            
            # Tables
            has_tables = False
            if hasattr(page_result, 'tables'):
                has_tables = len(page_result.tables) > 0
            
            # Language
            language = getattr(page_result, 'language', None)
            
            return {
                'page': page_num,
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
