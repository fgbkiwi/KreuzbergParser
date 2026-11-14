"""
KreuzbergParser sem interface gráfica.

Converte um PDF de autos do PJe em Markdown e grava o manifesto dos documentos
(`<nome>.manifesto.json`). Feito para ser chamado por outros programas.

Uso:
    python kreuzberg_cli.py autos.pdf --saida pasta/ --modo cpu [--vlm qwen3] [--json]

Com `--json`, a última linha da saída padrão é um objeto JSON com os caminhos
gerados; os registros de andamento vão para a saída de erro.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Mesmo preparo do main.py: OpenCV e ONNX Runtime antes de `import kreuzberg`.
sys.OpenCV_REPLACE_SYS_PATH_0 = True  # type: ignore[attr-defined]

MODOS = ("express", "cpu", "gpu", "paddle_cpu", "paddle_gpu")


def _argumentos(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kreuzberg_cli",
        description="Converte um PDF de autos do PJe em Markdown, sem interface gráfica.",
    )
    parser.add_argument("pdf", help="PDF a converter")
    parser.add_argument("--saida", help="pasta de destino (padrão: a pasta do PDF)")
    parser.add_argument("--modo", choices=MODOS, default="cpu", help="modo de OCR (padrão: cpu)")
    parser.add_argument(
        "--vlm",
        default="off",
        help="modelo de visão para formulários e imagens: off, qwen, qwen3, nemotron... "
        "(padrão: off)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="imprime na última linha um JSON com os arquivos gerados",
    )
    return parser.parse_args(argv)


def converter(pdf: Path, saida: Path, modo: str = "cpu", vlm: str = "off") -> dict:
    """Converte `pdf` e devolve os caminhos do Markdown e do manifesto."""
    from utils.ort_runtime import prepare_paddle_gpu_runtime

    prepare_paddle_gpu_runtime()

    from config import Config, ProcessingMode
    from core.kreuzberg_engine import KreuzbergOCREngine
    from core.manifest import write_manifest
    from core.markdown_converter import MarkdownConverter
    from utils.logger import setup_logger
    from utils.opencv_runtime import ensure_cv2
    from utils.poppler import ensure_poppler
    from utils.tessdata import ensure_tessdata

    config = Config()
    config.ensure_directories()
    setup_logger(config=config)
    # Os registros vão para a saída de erro: a saída padrão fica para o resultado.
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, "stream", None) is sys.stdout:
            handler.setStream(sys.stderr)
    logger = logging.getLogger("kreuzberg_cli")

    for preparo, descricao in (
        (ensure_cv2, "OpenCV"),
        (lambda: ensure_tessdata(config.TESSDATA_DIR, config.TESSERACT_LANGUAGES), "Tesseract"),
        (lambda: ensure_poppler(config.POPPLER_DIR), "Poppler"),
    ):
        try:
            preparo()
        except Exception:
            logger.exception("Não foi possível preparar %s", descricao)

    saida.mkdir(parents=True, exist_ok=True)
    vlm_backend = config.resolve_vlm_backend(vlm)
    enable_vlm = bool(config.vlm_preset(vlm_backend).get("enabled"))
    engine = KreuzbergOCREngine(ProcessingMode(modo), config)

    inicio = time.time()
    result = engine.process_pdf(
        pdf,
        images_output_dir=saida / f"{pdf.stem}_images",
        enable_vlm=enable_vlm,
        vlm_backend=vlm_backend,
    )
    run_suffix = result.get("metadata", {}).get("run_suffix") or modo
    carimbo = time.strftime("%Y-%m-%d_%H-%M-%S")
    converter_md = MarkdownConverter(config)
    md_path = Path(
        converter_md.convert_to_markdown(
            result, str(saida / f"{pdf.stem}_ocr_{run_suffix}_{carimbo}.md")
        )
    )
    manifesto = write_manifest(result, md_path, getattr(engine, "_sumario", None))
    stats = result.get("statistics", {})
    return {
        "markdown": str(md_path),
        "manifesto": str(manifesto),
        "processo": result.get("metadata", {}).get("processo"),
        "paginas": stats.get("total_pages", len(result.get("pages", []))),
        "paginas_com_falha_de_ocr": sum(1 for p in result.get("pages", []) if p.get("ocr_failed")),
        "tokens": getattr(converter_md, "last_token_count", None),
        "segundos": round(time.time() - inicio, 1),
    }


def main(argv=None) -> int:
    args = _argumentos(argv)
    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.is_file():
        print(f"PDF não encontrado: {pdf}", file=sys.stderr)
        return 2
    saida = Path(args.saida).expanduser().resolve() if args.saida else pdf.parent
    try:
        resultado = converter(pdf, saida, args.modo, args.vlm)
    except Exception as exc:
        logging.getLogger("kreuzberg_cli").exception("Falha na conversão")
        print(f"Falha na conversão: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, default=str))
    else:
        print(f"Markdown: {resultado['markdown']}")
        print(f"Manifesto: {resultado['manifesto']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
