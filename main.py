"""
Sistema Inteligente de OCR para PDFs Judiciais
Entry point - Powered by Kreuzberg

90% code reduction compared to original implementation
"""
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Kreuzberg native PaddleOCR GPU: onnxruntime-gpu + ORT_DYLIB_PATH before
# `import kreuzberg` (see docs.kreuzberg.dev GPU acceleration).
from utils.ort_runtime import prepare_paddle_gpu_runtime, log_ort_status

prepare_paddle_gpu_runtime()

from utils.logger import setup_logger
from utils.poppler import ensure_poppler
from utils.tessdata import ensure_tessdata
from config import Config
from ui.app import run_app


def main():
    """Main entry point"""
    # Configuration
    config = Config()
    config.ensure_directories()

    # Root logger so core.* / utils.* reach file + console
    setup_logger(config=config)
    logger = logging.getLogger(__name__)
    logger.info("=" * 70)
    logger.info("Sistema Inteligente de OCR para PDFs Judiciais")
    logger.info("Powered by Kreuzberg - https://kreuzberg.dev/")
    logger.info("=" * 70)
    logger.info("")
    logger.info("🚀 Code Reduction: 90% less custom code")
    logger.info("⚡ Performance: 3-5x faster with Rust core")
    logger.info("📦 Formats: 50+ file formats supported")
    logger.info("")

    # Kreuzberg embeds Tesseract; point it at local traineddata (por/eng)
    try:
        ensure_tessdata(config.TESSDATA_DIR, config.TESSERACT_LANGUAGES)
    except Exception:
        logger.exception(
            "Não foi possível preparar os dados de idioma do Tesseract. "
            "O OCR em páginas escaneadas pode falhar."
        )

    # Page classification needs Poppler (pdftotext/pdfinfo/pdfimages)
    try:
        ensure_poppler(config.POPPLER_DIR)
    except Exception:
        logger.exception(
            "Não foi possível preparar o Poppler. "
            "A classificação de páginas nativas vs escaneadas vai falhar."
        )

    # Kreuzberg native PaddleOCR GPU: ORT_DYLIB_PATH + onnxruntime-gpu
    try:
        log_ort_status()
    except Exception:
        logger.debug("ONNX Runtime bootstrap skipped", exc_info=True)

    # Log GPU status
    from utils.gpu_detector import gpu_detector
    gpu_detector.log_gpu_status()

    # Advisory dependency check (at most every 7 days; never blocks startup)
    try:
        from scripts.check_updates import maybe_check_dependency_updates
        maybe_check_dependency_updates(logger_=logger, background=True)
    except Exception:
        logger.debug("Checagem periódica de dependências ignorada", exc_info=True)

    logger.info("")
    logger.info("🎯 Iniciando interface gráfica...")

    # Launch Flet UI
    try:
        run_app()
    except KeyboardInterrupt:
        logger.info("Aplicação interrompida pelo usuário")
    except Exception:
        logger.exception("Erro fatal na aplicação")
        raise


if __name__ == "__main__":
    main()
