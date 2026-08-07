"""
Simplified configuration using Kreuzberg
90% reduction in configuration complexity
"""
import os
from pathlib import Path
from enum import Enum


class ProcessingMode(str, Enum):
    """Processing modes optimized for different hardware"""
    GPU = "gpu"
    CPU = "cpu"
    EXPRESS = "express"


class Config:
    """Simplified configuration with Kreuzberg integration"""
    
    # ===== PATHS =====
    BASE_DIR = Path(__file__).parent
    TEMP_DIR = BASE_DIR / "temp"
    LOG_DIR = BASE_DIR / "logs"
    OUTPUT_DIR = BASE_DIR / "output"
    # Local tessdata for Kreuzberg's embedded Tesseract (por/eng traineddata)
    TESSDATA_DIR = BASE_DIR / "tessdata"
    TESSERACT_LANGUAGES = ("por", "eng")
    
    # ===== KREUZBERG CONFIGURATION =====
    # Kreuzberg handles most thresholds automatically
    KREUZBERG_LANGUAGE = "por"  # Portuguese
    KREUZBERG_DETECT_TABLES = True
    KREUZBERG_EXTRACT_IMAGES = True
    KREUZBERG_LANGUAGE_DETECTION = True
    # Prefer physical PDF page boundaries over judicial "Fls.:" markers when
    # Kreuzberg does not return usable per-page content.
    USE_PHYSICAL_PAGE_EXTRACTION = True
    # Pages with less native text than this threshold may need OCR.
    MIN_NATIVE_PAGE_CHARS = 80

    # Backend-specific language code mapping
    # Example: PaddleOCR and EasyOCR use "pt" instead of Tesseract's "por" for Portuguese.
    BACKEND_LANGUAGE_MAP = {
        "paddleocr": {
            "por": "pt"
        },
        "easyocr": {
            "por": "pt"
        }
    }
    
    # ===== HANDWRITING DETECTION =====
    # Custom TrOCR for handwriting (only custom code needed)
    HANDWRITING_CONFIDENCE_THRESHOLD = 0.30
    TROCR_MODEL = 'microsoft/trocr-base-handwritten'
    TROCR_MAX_LENGTH = 256
    
    # ===== OCR MODE CONFIGURATIONS =====
    MODE_CONFIGS = {
        ProcessingMode.GPU: {
            "backend": "easyocr",  # GPU-accelerated
            "use_gpu": True,
            "batch_size": 4,
            "dpi": 200,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": True,  # Enable handwriting detection
            "force_ocr": False
        },
        ProcessingMode.CPU: {
            "backend": "tesseract",  # Fast CPU
            "use_gpu": False,
            "dpi": 300,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": False,  # Too slow on CPU
            "force_ocr": False
        },
        ProcessingMode.EXPRESS: {
            "backend": "tesseract",
            "use_gpu": False,
            "dpi": 150,  # Lower DPI for speed
            "detect_tables": False,
            "language": "por",
            "enable_trocr": False,
            "skip_preprocessing": True,
            "force_ocr": False
        }
    }
    
    # ===== GPU SETTINGS =====
    MIN_VRAM_GB = 2  # Minimum VRAM for GPU mode. Original value was 4. Lower values can cause OOM or slower/unstable runs.
    
    # ===== MARKDOWN SETTINGS =====
    INCLUDE_METADATA = True
    INCLUDE_PROCESSING_INFO = True
    INCLUDE_STATISTICS = True
    INCLUDE_TIMESTAMPS = True
    MD_ENCODING = 'utf-8'
    
    # ===== LOGGING =====
    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_TO_FILE = True
    LOG_TO_CONSOLE = True
    
    # ===== UI SETTINGS =====
    WINDOW_WIDTH = 1100
    WINDOW_HEIGHT = 830
    WINDOW_TITLE = "OCR Inteligente para PDFs Judiciais (Powered by Kreuzberg)"
    THEME_MODE = "light"
    
    @classmethod
    def ensure_directories(cls):
        """Create necessary directories"""
        cls.TEMP_DIR.mkdir(exist_ok=True)
        cls.LOG_DIR.mkdir(exist_ok=True)
        cls.OUTPUT_DIR.mkdir(exist_ok=True)
        cls.TESSDATA_DIR.mkdir(exist_ok=True)
    
    @classmethod
    def get_mode_config(cls, mode: ProcessingMode) -> dict:
        """Get configuration for specific processing mode"""
        return cls.MODE_CONFIGS.get(mode, cls.MODE_CONFIGS[ProcessingMode.EXPRESS])
