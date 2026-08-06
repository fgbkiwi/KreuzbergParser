"""
Sistema Inteligente de OCR para PDFs Judiciais
Entry point - Powered by Kreuzberg

90% code reduction compared to original implementation
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import setup_logger
from utils.tessdata import ensure_tessdata
from config import Config
from ui.app import run_app


def main():
    """Main entry point"""
    # Configuration
    config = Config()
    config.ensure_directories()
    
    # Setup logging
    logger = setup_logger(__name__, config)
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
    
    # Log GPU status
    from utils.gpu_detector import gpu_detector
    gpu_detector.log_gpu_status()
    
    logger.info("")
    logger.info("🎯 Iniciando interface gráfica...")
    
    # Launch Flet UI
    try:
        run_app()
    except KeyboardInterrupt:
        logger.info("Aplicação interrompida pelo usuário")
    except Exception as e:
        logger.exception("Erro fatal na aplicação")
        raise


if __name__ == "__main__":
    main()
