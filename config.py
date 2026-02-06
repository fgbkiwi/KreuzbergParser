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
    
    # ===== KREUZBERG CONFIGURATION =====
    # Kreuzberg handles most thresholds automatically
    KREUZBERG_LANGUAGE = "por"  # Portuguese
    KREUZBERG_DETECT_TABLES = True
    KREUZBERG_EXTRACT_IMAGES = True
    KREUZBERG_LANGUAGE_DETECTION = True
    
    # ===== HANDWRITING DETECTION =====
    # Custom TrOCR for handwriting (only custom code needed)
    HANDWRITING_CONFIDENCE_THRESHOLD = 0.30
    TROCR_MODEL = 'microsoft/trocr-base-handwritten'
    TROCR_MAX_LENGTH = 256
    
    # ===== OCR MODE CONFIGURATIONS =====
    MODE_CONFIGS = {
        ProcessingMode.GPU: {
            "backend": "paddleocr",  # GPU-accelerated
            "use_gpu": True,
            "batch_size": 4,
            "dpi": 300,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": True  # Enable handwriting detection
        },
        ProcessingMode.CPU: {
            "backend": "tesseract",  # Fast CPU
            "use_gpu": False,
            "dpi": 300,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": False  # Too slow on CPU
        },
        ProcessingMode.EXPRESS: {
            "backend": "tesseract",
            "use_gpu": False,
            "dpi": 150,  # Lower DPI for speed
            "detect_tables": False,
            "language": "por",
            "enable_trocr": False,
            "skip_preprocessing": True
        }
    }
    
    # ===== GPU SETTINGS =====
    MIN_VRAM_GB = 4  # Minimum VRAM for GPU mode
    
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
    WINDOW_WIDTH = 900
    WINDOW_HEIGHT = 750
    WINDOW_TITLE = "OCR Inteligente para PDFs Judiciais (Powered by Kreuzberg)"
    THEME_MODE = "light"
    
    @classmethod
    def ensure_directories(cls):
        """Create necessary directories"""
        cls.TEMP_DIR.mkdir(exist_ok=True)
        cls.LOG_DIR.mkdir(exist_ok=True)
        cls.OUTPUT_DIR.mkdir(exist_ok=True)
    
    @classmethod
    def get_mode_config(cls, mode: ProcessingMode) -> dict:
        """Get configuration for specific processing mode"""
        return cls.MODE_CONFIGS.get(mode, cls.MODE_CONFIGS[ProcessingMode.EXPRESS])
