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
    PADDLE_GPU = "paddle_gpu"
    PADDLE_CPU = "paddle_cpu"


def is_gpu_mode(mode) -> bool:
    """True for EasyOCR GPU and PaddleOCR GPU."""
    value = mode.value if isinstance(mode, ProcessingMode) else str(mode or "")
    return value in (ProcessingMode.GPU.value, ProcessingMode.PADDLE_GPU.value)


def is_paddle_mode(mode) -> bool:
    value = mode.value if isinstance(mode, ProcessingMode) else str(mode or "")
    return value in (
        ProcessingMode.PADDLE_GPU.value,
        ProcessingMode.PADDLE_CPU.value,
    )


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
    # Local Poppler binaries on Windows (pdftotext/pdfinfo/pdfimages)
    POPPLER_DIR = BASE_DIR / "poppler"
    
    # ===== KREUZBERG CONFIGURATION =====
    # Kreuzberg handles most thresholds automatically
    KREUZBERG_LANGUAGE = "por"  # Portuguese
    KREUZBERG_DETECT_TABLES = True
    KREUZBERG_EXTRACT_IMAGES = True
    KREUZBERG_LANGUAGE_DETECTION = True
    # Prefer physical PDF page boundaries over judicial "Fls.:" markers when
    # Kreuzberg does not return usable per-page content.
    USE_PHYSICAL_PAGE_EXTRACTION = True
    # Legacy threshold (pre-classifier). Prefer RESIDUAL_* below.
    MIN_NATIVE_PAGE_CHARS = 80
    # After stripping PJe stamps: residual text needed to treat page as native.
    RESIDUAL_NATIVE_CHARS = 200
    # Residual text + embedded figures → hybrid (petition with attachments).
    RESIDUAL_HYBRID_CHARS = 120
    # Image coverage vs A4 area to treat as full-page scan.
    FULL_PAGE_IMAGE_COVERAGE = 0.40
    MEDIUM_FIGURE_COVERAGE = 0.08

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
    # Default DPI for identity / standardized labor forms (TRCT, CD/SD, etc.)
    DPI_FORM_DEFAULT = 300

    MODE_CONFIGS = {
        ProcessingMode.GPU: {
            "backend": "easyocr",  # GPU-accelerated first pass
            "use_gpu": True,
            "batch_size": 4,
            "dpi": 300,
            "dpi_form": 300,
            "dpi_screenshot": 220,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": True,  # Enable handwriting detection
            "force_ocr": False
        },
        ProcessingMode.CPU: {
            "backend": "tesseract",  # Fast CPU
            "use_gpu": False,
            "dpi": 300,
            "dpi_form": 300,
            "dpi_screenshot": 220,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": False,  # Too slow on CPU
            "force_ocr": False
        },
        ProcessingMode.EXPRESS: {
            "backend": "tesseract",
            "use_gpu": False,
            "dpi": 200,
            "dpi_form": 250,
            "dpi_screenshot": 200,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": False,
            "skip_preprocessing": True,
            "force_ocr": False
        },
        ProcessingMode.PADDLE_GPU: {
            "backend": "paddleocr",
            "use_gpu": True,
            "batch_size": 4,
            "dpi": 300,
            "dpi_form": 300,
            "dpi_screenshot": 220,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": True,
            "force_ocr": False,
            "model_tier": "server",
            "rec_batch_num": 8,
        },
        ProcessingMode.PADDLE_CPU: {
            "backend": "paddleocr",
            "use_gpu": False,
            "batch_size": 1,
            "dpi": 300,
            "dpi_form": 300,
            "dpi_screenshot": 220,
            "detect_tables": True,
            "language": "por",
            "enable_trocr": False,
            "force_ocr": False,
            "model_tier": "mobile",
            "rec_batch_num": 6,
        },
    }

    # ===== LETTERHEAD / FOOTER (petitions) =====
    LETTERHEAD_TOP_RATIO = 0.18
    FOOTER_BOTTOM_RATIO = 0.15
    LETTERHEAD_REPEAT_MIN_PAGES = 3

    # ===== GPU SETTINGS =====
    MIN_VRAM_GB = 2  # Minimum VRAM for GPU mode. Original value was 4. Lower values can cause OOM or slower/unstable runs.
    GPU_BATCH_VRAM_GB = 8  # Full OCR batch (4) when VRAM is at least this.

    # ===== STRUCTURED FORM EXTRACTION (LlamaParse-style) =====
    # Deterministic Tesseract-TSV + geometry templates for TRCT / ficha / recibo / FGTS.
    ENABLE_TEMPLATE_EXTRACTION = True
    TEMPLATE_MIN_FILL_RATIO = 0.45
    # VLM fallback when a template cannot fill enough fields (local by default).
    ENABLE_VLM_FALLBACK = True
    # OpenAI-compatible endpoint. Ollama: http://127.0.0.1:11434/v1
    # vLLM Nemotron Parse: http://127.0.0.1:8000/v1
    # NVIDIA NIM (sends documents off-machine): https://integrate.api.nvidia.com/v1
    VLM_BASE_URL = os.environ.get("VLM_BASE_URL", "http://127.0.0.1:11434/v1")
    VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")
    VLM_API_KEY = os.environ.get("VLM_API_KEY", os.environ.get("NVIDIA_API_KEY", ""))
    VLM_TIMEOUT_S = float(os.environ.get("VLM_TIMEOUT_S", "90"))
    VLM_PRESETS = {
        "off": {
            "label": "Desligado (só templates)",
            "enabled": False,
            "base_url": "",
            "model": "",
        },
        "qwen": {
            "label": "Qwen2.5-VL (Ollama)",
            "enabled": True,
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "qwen2.5vl:7b",
        },
        "nemotron": {
            "label": "Nemotron Parse (vLLM)",
            "enabled": True,
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "nvidia/NVIDIA-Nemotron-Parse-2.0",
        },
    }
    # UI / CLI key: off | qwen | nemotron
    VLM_BACKEND = os.environ.get("VLM_BACKEND", "").strip().lower()

    @classmethod
    def resolve_vlm_backend(cls, backend: str | None = None) -> str:
        key = (backend or cls.VLM_BACKEND or "").strip().lower()
        if key in cls.VLM_PRESETS:
            return key
        if not cls.ENABLE_VLM_FALLBACK:
            return "off"
        model = (cls.VLM_MODEL or "").lower()
        url = (cls.VLM_BASE_URL or "").lower()
        if "nemotron" in model or ":8000" in url:
            return "nemotron"
        return "qwen"

    @classmethod
    def vlm_preset(cls, backend: str | None = None) -> dict:
        key = cls.resolve_vlm_backend(backend)
        preset = dict(cls.VLM_PRESETS[key])
        preset["key"] = key
        if key != "off":
            if cls.VLM_BASE_URL and (
                (key == "qwen" and "11434" in cls.VLM_BASE_URL)
                or (key == "nemotron" and "8000" in cls.VLM_BASE_URL)
                or (key == "nemotron" and "nvidia.com" in cls.VLM_BASE_URL)
            ):
                preset["base_url"] = cls.VLM_BASE_URL
            if cls.VLM_MODEL and (
                (key == "qwen" and "qwen" in cls.VLM_MODEL.lower())
                or (key == "nemotron" and "nemotron" in cls.VLM_MODEL.lower())
            ):
                preset["model"] = cls.VLM_MODEL
        return preset

    @classmethod
    def vlm_model_tag(cls, backend: str | None = None, *, enabled: bool | None = None) -> str:
        """Filename token for the VLM in use: nemotron | qwen | nenhum."""
        if enabled is False:
            return "nenhum"
        key = cls.resolve_vlm_backend(backend)
        if key == "nemotron":
            return "nemotron"
        if key == "qwen":
            return "qwen"
        return "nenhum"

    @classmethod
    def run_file_suffix(
        cls,
        mode,
        vlm_backend: str | None = None,
        *,
        enable_vlm: bool | None = None,
    ) -> str:
        """e.g. gpu_nemotron, cpu_qwen, express_nenhum"""
        if isinstance(mode, ProcessingMode):
            mode_key = mode.value
        else:
            mode_key = str(mode or "cpu").strip().lower() or "cpu"
        return f"{mode_key}_{cls.vlm_model_tag(vlm_backend, enabled=enable_vlm)}"
    
    # ===== MARKDOWN SETTINGS =====
    INCLUDE_METADATA = True
    INCLUDE_PROCESSING_INFO = False  # Per-page Tipo/Device/Imagens removed from MD body
    INCLUDE_STATISTICS = True
    INCLUDE_TIMESTAMPS = True
    # Do not embed raster/figure links in the Markdown body (text-only pages)
    EMBED_IMAGES_IN_MD = False
    MD_ENCODING = 'utf-8'
    
    # ===== LOGGING =====
    LOG_LEVEL = 'INFO'
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_TO_FILE = True
    LOG_TO_CONSOLE = True
    
    # ===== UI SETTINGS =====
    WINDOW_WIDTH = 1140
    WINDOW_HEIGHT = 920
    MODE_OPTIONS_PANEL_HEIGHT = 188
    WINDOW_TITLE = "OCR Inteligente para PDFs Judiciais (Powered by Kreuzberg)"
    THEME_MODE = "light"
    
    @classmethod
    def ensure_directories(cls):
        """Create necessary directories"""
        cls.TEMP_DIR.mkdir(exist_ok=True)
        cls.LOG_DIR.mkdir(exist_ok=True)
        cls.OUTPUT_DIR.mkdir(exist_ok=True)
        cls.TESSDATA_DIR.mkdir(exist_ok=True)
        cls.POPPLER_DIR.mkdir(exist_ok=True)
    
    @classmethod
    def get_mode_config(cls, mode: ProcessingMode) -> dict:
        """Get configuration for specific processing mode"""
        base = cls.MODE_CONFIGS.get(mode, cls.MODE_CONFIGS[ProcessingMode.EXPRESS])
        return dict(base)
