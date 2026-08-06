"""
Ensure Tesseract language data is available for Kreuzberg's embedded OCR.

Kreuzberg's bundled Tesseract looks under TESSDATA_PREFIX (or a broken
compile-time default like /io/.tesseract-cache/...). We keep traineddata
files under the project cache and point TESSDATA_PREFIX there.
"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

# Smaller/faster models suitable for OCR pipelines.
_TESSDATA_BASE_URL = (
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"
)
_DEFAULT_LANGS = ("por", "eng")


def ensure_tessdata(
    tessdata_dir: Path | str,
    languages: Sequence[str] | None = None,
) -> Path:
    """
    Download missing traineddata files and set TESSDATA_PREFIX.

    Returns the tessdata directory path.
    """
    target_dir = Path(tessdata_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    langs = tuple(languages or _DEFAULT_LANGS)
    missing = [lang for lang in langs if not _traineddata_path(target_dir, lang).exists()]

    if missing:
        logger.info("Baixando dados de idioma do Tesseract: %s", ", ".join(missing))
        _download_languages(target_dir, missing)
    else:
        logger.debug("Dados Tesseract já presentes em %s", target_dir)

    os.environ["TESSDATA_PREFIX"] = str(target_dir)
    logger.info("TESSDATA_PREFIX=%s", target_dir)
    return target_dir


def _traineddata_path(tessdata_dir: Path, lang: str) -> Path:
    return tessdata_dir / f"{lang}.traineddata"


def _download_languages(tessdata_dir: Path, languages: Iterable[str]) -> None:
    for lang in languages:
        url = _TESSDATA_BASE_URL.format(lang=lang)
        dest = _traineddata_path(tessdata_dir, lang)
        tmp = dest.with_suffix(".traineddata.partial")
        try:
            logger.info("Baixando %s...", url)
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(dest)
        except (urllib.error.URLError, OSError) as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Falha ao baixar '{lang}.traineddata'. "
                f"Baixe manualmente de {_TESSDATA_BASE_URL.format(lang=lang)} "
                f"e salve em {tessdata_dir}"
            ) from exc
