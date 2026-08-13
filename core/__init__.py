"""
Core modules initialization
"""
from .kreuzberg_engine import KreuzbergOCREngine
from .handwriting_detector import HandwritingDetector
from .markdown_converter import MarkdownConverter
from .form_templates import try_structured_extraction, ExtractionResult
from .vlm_ocr import parse_page_image, vlm_available
from .page_classifier import (
    classify_page,
    classify_pdf_pages,
    extract_cnj_process_number,
    strip_pje_boilerplate,
)
from .pje_sumario import (
    load_sumario_for_pdf,
    parse_sumario_from_pages,
    resolve_entry_for_page,
)

__all__ = [
    "KreuzbergOCREngine",
    "HandwritingDetector",
    "MarkdownConverter",
    "try_structured_extraction",
    "ExtractionResult",
    "parse_page_image",
    "vlm_available",
    "classify_page",
    "classify_pdf_pages",
    "extract_cnj_process_number",
    "strip_pje_boilerplate",
    "load_sumario_for_pdf",
    "parse_sumario_from_pages",
    "resolve_entry_for_page",
]
