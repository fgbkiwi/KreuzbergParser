"""
Kreuzberg-powered OCR Engine — page-by-page pipeline for PJe PDFs.

Uses PJe SUMÁRIO Tipo (when available), classifies pages after stripping
PJe/Unico stamps, OCRs image-dominant pages and hybrid figures, and
formats known labor forms.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import kreuzberg
    KREUZBERG_AVAILABLE = True
except ImportError:
    KREUZBERG_AVAILABLE = False
    logging.warning("Kreuzberg not installed. Install with: pip install kreuzberg")

from config import Config, ProcessingMode
from core.form_templates import TEMPLATE_KINDS, try_structured_extraction
from core.labor_forms import format_labor_document, refine_kind
from core.page_classifier import (
    classify_pdf_pages,
    extract_cnj_process_number,
    extract_page_images_to_dir,
)
from core.pje_sumario import (
    detect_doc_kind_from_text,
    extract_signature_ids,
    load_sumario_for_pdf,
)
from utils.gpu_detector import gpu_detector
from utils.logger import attach_process_log_file, log_page_end, log_page_start
from utils.pdf_pages import extract_pages_text, get_page_count, poppler_available
from utils.tessdata import ensure_tessdata

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, Dict], None]

# Document kinds that benefit from high DPI + Tesseract tables
FORM_KINDS = {
    "trct",
    "cd_sd",
    "ficha_registro",
    "recibo",
    "fgts",
    "cnh",
    "identidade",
    "ctps",
}


class KreuzbergOCREngine:
    """Page-aware OCR engine using Kreuzberg + Poppler classification."""

    def __init__(self, mode: ProcessingMode, config: Config = None):
        if not KREUZBERG_AVAILABLE:
            raise ImportError(
                "Kreuzberg library not installed. Run: pip install kreuzberg"
            )

        self.mode = mode
        self.config = config or Config()
        self.mode_config = self.config.get_mode_config(mode)
        self._handwriting_detector = None
        self._sumario = {}
        self.enable_template_extraction = bool(
            getattr(self.config, "ENABLE_TEMPLATE_EXTRACTION", True)
        )
        self.enable_vlm_fallback = bool(
            getattr(self.config, "ENABLE_VLM_FALLBACK", True)
        )
        preset = self.config.vlm_preset()
        self.vlm_base_url = preset.get("base_url") or getattr(
            self.config, "VLM_BASE_URL", ""
        )
        self.vlm_model = preset.get("model") or getattr(self.config, "VLM_MODEL", "")

        if mode == ProcessingMode.GPU:
            is_suitable, message = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
            if not is_suitable:
                logger.warning(
                    "GPU inadequate: %s. Falling back to CPU mode.", message
                )
                self.mode = ProcessingMode.CPU
                self.mode_config = self.config.get_mode_config(ProcessingMode.CPU)

        logger.info("Kreuzberg OCR Engine initialized in %s mode", self.mode)
        logger.info("Backend: %s", self.mode_config.get("backend", "tesseract"))

        if self.mode_config.get("backend", "tesseract") == "tesseract":
            try:
                ensure_tessdata(
                    self.config.TESSDATA_DIR, self.config.TESSERACT_LANGUAGES
                )
            except Exception as exc:
                logger.warning("Tessdata setup failed: %s", exc)

    def process_pdf(
        self,
        pdf_path: str,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        enable_handwriting: bool = False,
        images_output_dir: Optional[str | Path] = None,
        enable_vlm: Optional[bool] = None,
        enable_templates: Optional[bool] = None,
        vlm_backend: Optional[str] = None,
        vlm_base_url: Optional[str] = None,
        vlm_model: Optional[str] = None,
    ) -> Dict:
        start_time = time.time()
        pdf_path = Path(pdf_path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        processo = (
            extract_cnj_process_number(pdf_path=pdf_path) or pdf_path.stem
        )

        if enable_templates is not None:
            self.enable_template_extraction = bool(enable_templates)
        if vlm_backend is not None:
            preset = self.config.vlm_preset(vlm_backend)
            self.enable_vlm_fallback = bool(preset.get("enabled"))
            self.vlm_base_url = preset.get("base_url") or ""
            self.vlm_model = preset.get("model") or ""
        if enable_vlm is not None:
            self.enable_vlm_fallback = bool(enable_vlm)
        if vlm_base_url:
            self.vlm_base_url = vlm_base_url
        if vlm_model:
            self.vlm_model = vlm_model

        run_suffix = self.config.run_file_suffix(
            self.mode,
            vlm_backend,
            enable_vlm=self.enable_vlm_fallback,
        )
        log_path = attach_process_log_file(
            processo, self.config, suffix=run_suffix
        )
        logger.info("Processing PDF: %s (processo=%s)", pdf_path.name, processo)
        logger.info("Audit log: %s", log_path)

        if images_output_dir is None:
            images_dir = pdf_path.parent / f"{pdf_path.stem}_images"
        else:
            images_dir = Path(images_output_dir)
        images_dir.mkdir(parents=True, exist_ok=True)

        use_handwriting = bool(
            enable_handwriting
            and self.mode == ProcessingMode.GPU
            and self.mode_config.get("enable_trocr", False)
        )
        if use_handwriting:
            self._ensure_handwriting_detector()

        logger.info(
            "Form templates=%s VLM fallback=%s model=%s url=%s suffix=%s",
            self.enable_template_extraction,
            self.enable_vlm_fallback,
            self.vlm_model or "-",
            self.vlm_base_url or "-",
            run_suffix,
        )

        try:
            if not poppler_available():
                raise RuntimeError(
                    "Poppler (pdftotext/pdfinfo) is required for page classification"
                )

            page_texts = extract_pages_text(pdf_path)
            total_pages = len(page_texts) or get_page_count(pdf_path)
            if not page_texts:
                page_texts = [""] * total_pages

            self._sumario = load_sumario_for_pdf(pdf_path, page_texts)
            logger.info("SUMÁRIO entries: %s", len(self._sumario))

            classifications = classify_pdf_pages(
                pdf_path,
                page_texts,
                residual_native_chars=self.config.RESIDUAL_NATIVE_CHARS,
                residual_hybrid_chars=self.config.RESIDUAL_HYBRID_CHARS,
                sumario=self._sumario,
                full_page_coverage=self.config.FULL_PAGE_IMAGE_COVERAGE,
                medium_figure_coverage=self.config.MEDIUM_FIGURE_COVERAGE,
            )

            pages_data: List[Dict] = []
            for classification in classifications:
                page_data = self._process_classified_page(
                    pdf_path=pdf_path,
                    processo=processo,
                    classification=classification,
                    raw_text=page_texts[classification.page - 1]
                    if classification.page - 1 < len(page_texts)
                    else "",
                    images_dir=images_dir,
                    enable_handwriting=use_handwriting,
                )
                pages_data.append(page_data)
                if progress_callback:
                    try:
                        progress_callback(
                            classification.page, total_pages, page_data
                        )
                    except Exception as cb_exc:
                        logger.debug("Progress callback error: %s", cb_exc)

            total_time = time.time() - start_time
            stats = self._calculate_statistics(pages_data, total_time)

            return {
                "pages": pages_data,
                "statistics": stats,
                "metadata": {
                    "filename": pdf_path.name,
                    "processo": processo,
                    "total_pages": stats.get("total_pages", len(pages_data)),
                    "mode": self.mode,
                    "backend": self.mode_config.get("backend"),
                    "processing_time": total_time,
                    "images_dir": str(images_dir),
                    "log_path": str(log_path),
                    "run_suffix": run_suffix,
                    "handwriting_enabled": use_handwriting,
                    "sumario_docs": len(self._sumario),
                    "template_extraction": self.enable_template_extraction,
                    "vlm_fallback": self.enable_vlm_fallback,
                    "vlm_model": self.vlm_model,
                    "vlm_base_url": self.vlm_base_url,
                },
            }
        except Exception:
            logger.exception("Error processing PDF: %s", pdf_path.name)
            raise

    # ------------------------------------------------------------------
    # Per-page processing
    # ------------------------------------------------------------------

    def _process_classified_page(
        self,
        *,
        pdf_path: Path,
        processo: str,
        classification,
        raw_text: str,
        images_dir: Path,
        enable_handwriting: bool,
    ) -> Dict:
        page_num = classification.page
        page_class = classification.page_class
        subtype = classification.subtype
        fls = classification.stamp.fls
        stamp = classification.stamp
        sig_ids = list(stamp.signature_ids or extract_signature_ids(raw_text or ""))

        device, library = self._device_library_for_class(page_class)

        inicio_dt = datetime.now()
        inicio_iso = inicio_dt.isoformat(timespec="seconds")
        t0 = time.time()

        log_page_start(
            logger,
            processo=processo,
            pagina=page_num,
            fls=fls,
            device=device,
            library=library,
            tipo=page_class,
            subtype=subtype,
        )

        page_data: Dict = {
            "page": page_num,
            "marker_page_number": fls,
            "fls": fls,
            "type": page_class,
            "subtype": subtype,
            "device": device,
            "library": library,
            "pje_stamp": stamp.raw_stamp,
            "signature": stamp.signature,
            "signature_ids": sig_ids,
            "validation_url": stamp.validation_url,
            "document_number": stamp.document_number,
            "doc_kind": classification.doc_kind,
            "sumario_tipo": classification.sumario_tipo,
            "sumario_titulo": classification.sumario_titulo,
            "has_tables": False,
            "language": None,
            "images": [],
            "raster_path": None,
            "ocr_failed": False,
            "ocr_failure_reason": None,
            "handwriting_detected": False,
        }

        try:
            if page_class == "native":
                page_data.update(self._process_native_page(classification, raw_text))
            elif page_class == "hybrid":
                page_data.update(
                    self._process_hybrid_page(
                        pdf_path, classification, images_dir, raw_text
                    )
                )
            else:
                page_data.update(
                    self._process_image_page(
                        pdf_path,
                        classification,
                        images_dir,
                        enable_handwriting=enable_handwriting,
                        raw_text=raw_text,
                    )
                )
                page_data["device"] = self._ocr_device()
                page_data["library"] = self._ocr_library()
                device = page_data["device"]
                library = page_data["library"]
        except Exception as exc:
            logger.exception("Page %s processing failed: %s", page_num, exc)
            page_data["ocr_failed"] = True
            page_data["ocr_failure_reason"] = str(exc)
            page_data["text"] = (classification.residual_text or "").strip()
            page_data["signature_lines"] = self._signature_lines_from_raw(raw_text)

        # Ensure signature metadata for MD post-processing
        if "signature_lines" not in page_data:
            page_data["signature_lines"] = self._signature_lines_from_raw(raw_text)
        if not page_data.get("signature_ids"):
            page_data["signature_ids"] = sig_ids

        text = page_data.get("text") or ""
        page_data["word_count"] = len(text.split()) if text.strip() else 0
        page_data["char_count"] = len(text) if text else 0

        fim_dt = datetime.now()
        fim_iso = fim_dt.isoformat(timespec="seconds")
        duration = time.time() - t0
        page_data["processing_time"] = round(duration, 3)
        page_data["started_at"] = inicio_iso
        page_data["finished_at"] = fim_iso

        log_page_end(
            logger,
            processo=processo,
            pagina=page_num,
            fls=fls,
            device=device,
            library=library,
            tipo=page_class,
            inicio=inicio_iso,
            fim=fim_iso,
            duracao_s=duration,
            chars=page_data["char_count"],
            subtype=subtype,
        )
        return page_data

    def _process_native_page(self, classification, raw_text: str) -> Dict:
        body = (classification.residual_text or "").strip()
        kind = classification.doc_kind or detect_doc_kind_from_text(body)
        if kind:
            body = format_labor_document(body, kind)
        return {
            "text": body,
            "residual_text": classification.residual_text,
            "type": "native",
            "device": "CPU",
            "library": "poppler.pdftotext",
            "doc_kind": kind,
            "signature_lines": self._signature_lines_from_raw(raw_text),
        }

    def _process_hybrid_page(
        self,
        pdf_path: Path,
        classification,
        images_dir: Path,
        raw_text: str,
    ) -> Dict:
        body = (classification.residual_text or "").strip()
        figure_texts: List[str] = []
        image_paths: List[str] = []

        saved = extract_page_images_to_dir(
            pdf_path,
            classification.page,
            images_dir,
            prefix="hybrid",
            skip_logos=True,
        )
        # Prefer content figures by size; skip tiny leftovers
        for path in saved:
            if path.stat().st_size < 3000:
                continue
            image_paths.append(str(path))
            try:
                ocr_text, has_tables, _lang, table_mds = self._ocr_image_file(
                    path, prefer_tables=True, dpi_hint=300
                )
                if ocr_text.strip():
                    formatted = format_labor_document(
                        ocr_text,
                        detect_doc_kind_from_text(ocr_text),
                        tables_markdown=table_mds or None,
                    )
                    figure_texts.append(formatted)
                    if has_tables:
                        # flag bubbled via return
                        pass
            except Exception as exc:
                logger.warning(
                    "Hybrid figure OCR failed page %s (%s): %s",
                    classification.page,
                    path.name,
                    exc,
                )

        parts = []
        if body:
            parts.append(body)
        for idx, fig in enumerate(figure_texts, start=1):
            parts.append(f"### Quadro / figura {idx}\n\n{fig}")
        text = "\n\n".join(parts).strip()

        return {
            "text": text,
            "residual_text": classification.residual_text,
            "type": "hybrid",
            "device": "CPU",
            "library": "poppler.pdftotext+ocr_figures",
            "images": image_paths if self.config.EMBED_IMAGES_IN_MD else [],
            "has_tables": any("|" in (f or "") for f in figure_texts),
            "doc_kind": classification.doc_kind,
            "signature_lines": self._signature_lines_from_raw(raw_text),
        }

    def _process_image_page(
        self,
        pdf_path: Path,
        classification,
        images_dir: Path,
        *,
        enable_handwriting: bool,
        raw_text: str,
    ) -> Dict:
        kind = classification.doc_kind
        dpi = self._dpi_for_kind(kind, classification.subtype)

        page_index = classification.page - 1
        try:
            png_bytes = kreuzberg.render_pdf_page(
                str(pdf_path), page_index, dpi=dpi
            )
        except Exception as exc:
            raise RuntimeError(f"render_pdf_page failed: {exc}") from exc

        raster_path = images_dir / f"raster_page_{classification.page:04d}.png"
        raster_path.write_bytes(png_bytes)

        ocr_text = ""
        has_tables = False
        language = None
        table_mds: List[str] = []
        ocr_failed = False
        failure_reason = None

        prefer_tables = bool(kind in FORM_KINDS or classification.force_image_ocr)

        try:
            ocr_text, has_tables, language, table_mds = self._ocr_png_bytes(
                png_bytes,
                prefer_tables=prefer_tables,
                form_kind=kind,
            )
        except Exception as exc:
            error_text = str(exc)
            if self.mode == ProcessingMode.GPU and "easyocr" in error_text.lower():
                logger.warning(
                    "GPU OCR failed on page %s (%s). Falling back to CPU.",
                    classification.page,
                    error_text,
                )
                self.mode = ProcessingMode.CPU
                self.mode_config = self.config.get_mode_config(ProcessingMode.CPU)
                try:
                    ensure_tessdata(
                        self.config.TESSDATA_DIR, self.config.TESSERACT_LANGUAGES
                    )
                except Exception:
                    pass
                try:
                    ocr_text, has_tables, language, table_mds = self._ocr_png_bytes(
                        png_bytes,
                        prefer_tables=True,
                        form_kind=kind,
                        force_tesseract=True,
                    )
                except Exception as cpu_exc:
                    ocr_failed = True
                    failure_reason = f"ocr_failed: {cpu_exc}"
            else:
                ocr_failed = True
                failure_reason = f"ocr_failed: {exc}"

        if not ocr_failed and not (ocr_text or "").strip() and not table_mds:
            if not (classification.residual_text or "").strip():
                ocr_failed = True
                failure_reason = failure_reason or "empty_after_ocr"

        body = (ocr_text or "").strip()
        if not body and classification.residual_text.strip():
            body = classification.residual_text.strip()

        # Resolve kind: sumario first, then OCR keywords
        kind = refine_kind(kind, body) or detect_doc_kind_from_text(body)
        if kind in FORM_KINDS and not table_mds and self.mode_config.get("backend") != "tesseract":
            # Second pass with Tesseract for table layout
            try:
                ensure_tessdata(
                    self.config.TESSDATA_DIR, self.config.TESSERACT_LANGUAGES
                )
            except Exception:
                pass
            try:
                t_text, t_has, _lang, t_tables = self._ocr_png_bytes(
                    png_bytes,
                    prefer_tables=True,
                    form_kind=kind,
                    force_tesseract=True,
                )
                if t_tables or (t_text and len(t_text) >= len(body) * 0.6):
                    if t_tables:
                        table_mds = t_tables
                        has_tables = True
                    if t_text.strip():
                        body = t_text.strip()
                    has_tables = has_tables or t_has
            except Exception as tess_exc:
                logger.debug(
                    "Tesseract second pass skipped page %s: %s",
                    classification.page,
                    tess_exc,
                )

        extraction_source = None
        use_structured = self.enable_template_extraction and (
            kind in FORM_KINDS
            or kind in TEMPLATE_KINDS
            or classification.force_image_ocr
        )
        if use_structured and png_bytes:
            try:
                structured = try_structured_extraction(
                    png_bytes,
                    kind=kind,
                    ocr_text=body,
                    min_fill_ratio=float(
                        getattr(self.config, "TEMPLATE_MIN_FILL_RATIO", 0.45)
                    ),
                    enable_vlm=self.enable_vlm_fallback,
                    vlm_base_url=self.vlm_base_url or None,
                    vlm_model=self.vlm_model or None,
                )
            except Exception as struct_exc:
                logger.warning(
                    "Structured extraction failed page %s: %s",
                    classification.page,
                    struct_exc,
                )
                structured = None
            if structured and structured.ok and structured.markdown.strip():
                body = structured.markdown.strip()
                extraction_source = structured.source
                has_tables = has_tables or structured.n_table_rows > 0
                logger.info(
                    "Page %s structured via %s (fill=%.2f kind=%s)",
                    classification.page,
                    structured.source,
                    structured.fill_ratio,
                    structured.kind,
                )
            elif kind:
                body = format_labor_document(
                    body, kind, tables_markdown=table_mds or None
                )
                extraction_source = "labor_forms"
        elif kind:
            body = format_labor_document(
                body, kind, tables_markdown=table_mds or None
            )
            extraction_source = "labor_forms"

        images: List[str] = []
        if self.config.EMBED_IMAGES_IN_MD:
            images = [str(raster_path)]

        result = {
            "text": body,
            "residual_text": classification.residual_text,
            "ocr_text": ocr_text,
            "type": "image_page",
            "device": self._ocr_device(),
            "library": self._ocr_library(force_tesseract=False),
            "has_tables": has_tables or bool(table_mds),
            "language": language,
            "images": images,
            "raster_path": str(raster_path),
            "ocr_failed": ocr_failed,
            "ocr_failure_reason": failure_reason,
            "doc_kind": kind,
            "extraction_source": extraction_source,
            "signature_lines": self._signature_lines_from_raw(raw_text),
        }

        if enable_handwriting and self._handwriting_detector and not ocr_failed:
            try:
                result = self._handwriting_detector.enhance_page_with_handwriting_detection(
                    result, str(raster_path)
                )
                # Strip stamp-like additions if any
                result["text"] = (result.get("text") or "").strip()
            except Exception as hw_exc:
                logger.warning(
                    "TrOCR failed on page %s: %s", classification.page, hw_exc
                )

        return result

    def _dpi_for_kind(self, kind: Optional[str], subtype: str) -> int:
        if subtype == "print_chat":
            return int(self.mode_config.get("dpi_screenshot", 220))
        if kind in FORM_KINDS or kind == "cnh":
            return int(self.mode_config.get("dpi_form", self.config.DPI_FORM_DEFAULT))
        return int(self.mode_config.get("dpi", 300))

    def _ocr_image_file(
        self,
        path: Path,
        *,
        prefer_tables: bool,
        dpi_hint: int = 300,
    ):
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        _ = dpi_hint
        return self._ocr_png_bytes(
            data,
            prefer_tables=prefer_tables,
            mime_type=mime,
            force_tesseract=prefer_tables,
        )

    def _ocr_png_bytes(
        self,
        png_bytes: bytes,
        *,
        prefer_tables: bool = False,
        form_kind: Optional[str] = None,
        force_tesseract: bool = False,
        mime_type: str = "image/png",
    ):
        """Run Kreuzberg OCR on image bytes."""
        extraction_config, easyocr_kwargs = self._build_extraction_config(
            force_ocr=True,
            prefer_tables=prefer_tables or bool(form_kind in FORM_KINDS),
            force_tesseract=force_tesseract,
        )
        kwargs = {"config": extraction_config}
        if easyocr_kwargs is not None and not force_tesseract:
            kwargs["easyocr_kwargs"] = easyocr_kwargs

        result = kreuzberg.extract_bytes_sync(png_bytes, mime_type, **kwargs)

        text = ""
        if hasattr(result, "content") and result.content:
            text = str(result.content)
        elif hasattr(result, "text"):
            text = str(result.text or "")

        tables = getattr(result, "tables", None) or []
        has_tables = bool(tables)
        language = getattr(result, "language", None)
        if language is None and hasattr(result, "metadata"):
            meta = result.metadata
            if isinstance(meta, dict):
                language = meta.get("language")

        table_mds: List[str] = []
        if tables:
            for table in tables:
                md = getattr(table, "markdown", None) or getattr(table, "text", None)
                if md:
                    table_mds.append(str(md))

        return text.strip(), has_tables, language, table_mds

    # ------------------------------------------------------------------
    # Config / helpers
    # ------------------------------------------------------------------

    def _build_extraction_config(
        self,
        *,
        force_ocr: bool = False,
        prefer_tables: bool = False,
        force_tesseract: bool = False,
    ):
        language = self.mode_config.get("language", "por")
        backend = self.mode_config.get("backend", "tesseract")
        if force_tesseract:
            backend = "tesseract"

        language = self.config.BACKEND_LANGUAGE_MAP.get(backend, {}).get(
            language, language
        )

        easyocr_kwargs = None
        use_gpu = bool(self.mode_config.get("use_gpu", False)) and not force_tesseract

        ocr_config = None
        if backend == "tesseract":
            tesseract_kwargs = {
                "language": language,
                "enable_table_detection": prefer_tables
                or self.mode_config.get("detect_tables", True),
            }
            # PSM 6 = assume uniform block of text (forms)
            try:
                tesseract_kwargs["psm"] = 6 if prefer_tables else None
            except Exception:
                pass
            tesseract_config = kreuzberg.TesseractConfig(
                **{k: v for k, v in tesseract_kwargs.items() if v is not None}
            )
            ocr_config = kreuzberg.OcrConfig(
                backend="tesseract",
                language=language,
                tesseract_config=tesseract_config,
            )
        elif backend == "paddleocr":
            ocr_config = kreuzberg.OcrConfig(
                backend="paddleocr",
                language=language,
                paddle_ocr_config=kreuzberg.PaddleOcrConfig(
                    language=language,
                    enable_table_detection=prefer_tables
                    or self.mode_config.get("detect_tables", True),
                ),
            )
        elif backend == "easyocr":
            ocr_config = kreuzberg.OcrConfig(backend="easyocr", language=language)
            if use_gpu:
                easyocr_kwargs = {"use_gpu": True}

        language_detection = kreuzberg.LanguageDetectionConfig(
            enabled=self.config.KREUZBERG_LANGUAGE_DETECTION
        )
        images = kreuzberg.ImageExtractionConfig(
            extract_images=False,
            target_dpi=self.mode_config.get("dpi", 300),
        )
        pdf_options = kreuzberg.PdfConfig(extract_images=False)
        pages = kreuzberg.PageConfig(extract_pages=False, insert_page_markers=False)

        acceleration = None
        if use_gpu and backend == "easyocr":
            acceleration = kreuzberg.AccelerationConfig(provider="cuda")

        config = kreuzberg.ExtractionConfig(
            ocr=ocr_config,
            language_detection=language_detection,
            images=images,
            pdf_options=pdf_options,
            pages=pages,
            force_ocr=force_ocr or self.mode_config.get("force_ocr", False),
            acceleration=acceleration,
        )
        return config, easyocr_kwargs

    def _device_library_for_class(self, page_class: str) -> tuple[str, str]:
        if page_class == "native":
            return "CPU", "poppler.pdftotext"
        if page_class == "hybrid":
            return "CPU", "poppler.pdftotext+ocr_figures"
        return self._ocr_device(), self._ocr_library()

    def _ocr_device(self) -> str:
        if self.mode == ProcessingMode.GPU and self.mode_config.get("use_gpu"):
            return "GPU"
        return "CPU"

    def _ocr_library(self, force_tesseract: bool = False) -> str:
        if force_tesseract:
            return "kreuzberg+tesseract"
        backend = self.mode_config.get("backend", "tesseract")
        if backend == "easyocr":
            return "kreuzberg+easyocr"
        if backend == "paddleocr":
            return "kreuzberg+paddleocr"
        return "kreuzberg+tesseract"

    def _signature_lines_from_raw(self, raw_text: str) -> List[str]:
        import re

        lines = []
        for match in re.finditer(
            r"(?mi)^\s*Documento assinado eletronicamente por .+?$",
            raw_text or "",
        ):
            lines.append(match.group(0).strip())
        return lines

    def _ensure_handwriting_detector(self) -> None:
        if self._handwriting_detector is not None:
            return
        try:
            from core.handwriting_detector import HandwritingDetector

            self._handwriting_detector = HandwritingDetector(self.config)
        except Exception as exc:
            logger.warning("Handwriting detector unavailable: %s", exc)
            self._handwriting_detector = None

    def _calculate_statistics(self, pages: List[Dict], total_time: float) -> Dict:
        total_pages = len(pages)
        native_count = sum(1 for p in pages if p.get("type") == "native")
        hybrid_count = sum(1 for p in pages if p.get("type") == "hybrid")
        image_count = sum(1 for p in pages if p.get("type") == "image_page")
        scanned_count = image_count
        pages_with_tables = sum(1 for p in pages if p.get("has_tables", False))
        ocr_failed = sum(1 for p in pages if p.get("ocr_failed"))
        total_chars = sum(p.get("char_count", 0) for p in pages)
        total_words = sum(p.get("word_count", 0) for p in pages)

        return {
            "total_pages": total_pages,
            "native_pages": native_count,
            "hybrid_pages": hybrid_count,
            "image_pages": image_count,
            "scanned_pages": scanned_count,
            "pages_with_tables": pages_with_tables,
            "ocr_failed_pages": ocr_failed,
            "total_characters": total_chars,
            "total_words": total_words,
            "total_time": round(total_time, 2),
            "avg_time_per_page": round(total_time / total_pages, 2)
            if total_pages
            else 0,
            "processing_speed": (
                f"{total_pages / total_time:.2f} pages/sec"
                if total_time > 0
                else "N/A"
            ),
        }
