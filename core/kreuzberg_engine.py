"""
Kreuzberg-powered OCR Engine — page-by-page pipeline for PJe PDFs.

Uses PJe SUMÁRIO Tipo (when available), classifies pages after stripping
PJe/Unico stamps, OCRs image-dominant pages and hybrid figures, and
formats known labor forms.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    import kreuzberg as _kreuzberg_mod
    KREUZBERG_AVAILABLE = True
except ImportError:
    _kreuzberg_mod = None
    KREUZBERG_AVAILABLE = False
    logging.warning("Kreuzberg not installed. Install with: pip install kreuzberg")

# Any: ImportError leaves the module unbound; callers check KREUZBERG_AVAILABLE.
kreuzberg: Any = _kreuzberg_mod

from config import Config, ProcessingMode, is_gpu_mode
from core.form_templates import TEMPLATE_KINDS, try_structured_extraction
from core.labor_forms import format_labor_document, refine_kind
from core.letterhead import apply_letterhead_strip
from core.page_classifier import (
    classify_pdf_pages,
    collect_fls_numbers,
    extract_cnj_process_number,
    extract_page_images_to_dir,
    prefer_process_folio,
)
from core.page_layout import (
    LayoutRect,
    is_duplicate_figure_text,
    load_pdf_layouts,
    native_text_covers_rect,
)
from core.pje_sumario import (
    detect_doc_kind_from_text,
    extract_signature_ids,
    load_sumario_for_pdf,
)
from utils.gpu_detector import gpu_detector
from utils.logger import attach_process_log_file, log_page_end, log_page_start
from utils.pdf_pages import extract_pages_text, get_page_count, poppler_available
from utils.poppler import ensure_poppler
from utils.tessdata import ensure_tessdata

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, Dict], None]

# The native CUDA probe costs a full PaddleOCR model load (~25s), so its verdict
# is reused for the lifetime of the process.
_NATIVE_PADDLE_CUDA_PROBE: Optional[Tuple[bool, Optional[BaseException], object]] = None

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

    def __init__(self, mode: ProcessingMode, config: Optional[Config] = None):
        if not KREUZBERG_AVAILABLE or kreuzberg is None:
            raise ImportError(
                "Kreuzberg library not installed. Run: pip install kreuzberg"
            )

        self.mode = mode
        self.config = config or Config()
        self.mode_config = self.config.get_mode_config(mode)
        self._handwriting_detector = None
        self._paddle_gpu = None
        self._paddle_native_cuda_error: Optional[BaseException] = None
        self._paddle_gpu_fallback_lines: List[str] = []
        self._sumario = {}
        self._layouts = {}
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

        if mode == ProcessingMode.PADDLE_GPU:
            is_suitable, message = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
            if not is_suitable:
                raise RuntimeError(
                    "PaddleOCR GPU recusado: %s. "
                    "Este modo não cai para CPU — escolha PaddleOCR CPU ou "
                    "corrija o driver NVIDIA."
                    % message
                )
            self._init_paddle_gpu()
        elif is_gpu_mode(mode):
            is_suitable, message = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
            if not is_suitable:
                logger.warning(
                    "GPU inadequate: %s. Falling back to %s mode.",
                    message,
                    ProcessingMode.CPU.value,
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
        pdf_path: str | Path,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        enable_handwriting: bool = False,
        images_output_dir: Optional[str | Path] = None,
        enable_vlm: Optional[bool] = None,
        enable_templates: Optional[bool] = None,
        vlm_backend: Optional[str] = None,
        vlm_base_url: Optional[str] = None,
        vlm_model: Optional[str] = None,
        retarget_session: bool = True,
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

        # Before run_file_suffix, so the log/output names match what really ran.
        if self.enable_vlm_fallback:
            self._check_vlm_endpoint()

        run_suffix = self.config.run_file_suffix(
            self.mode,
            vlm_backend,
            enable_vlm=self.enable_vlm_fallback,
        )
        log_path = attach_process_log_file(
            processo,
            self.config,
            suffix=run_suffix,
            retarget_session=retarget_session,
        )
        logger.info("Processing PDF: %s (processo=%s)", pdf_path.name, processo)
        logger.info("Audit log: %s", log_path)
        self._log_paddle_gpu_fallback_alert(etapa="início do processamento")

        if images_output_dir is None:
            images_dir = pdf_path.parent / f"{pdf_path.stem}_images"
        else:
            images_dir = Path(images_output_dir)
        images_dir.mkdir(parents=True, exist_ok=True)

        use_handwriting = bool(
            enable_handwriting
            and is_gpu_mode(self.mode)
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
            try:
                ensure_poppler(self.config.POPPLER_DIR)
            except Exception as exc:
                logger.warning("Poppler setup failed: %s", exc)

            if not poppler_available():
                raise RuntimeError(
                    "Poppler (pdftotext/pdfinfo) é necessário para classificar páginas. "
                    "No Windows o app baixa automaticamente para a pasta poppler/. "
                    "No Linux: sudo apt install poppler-utils. "
                    "No macOS: brew install poppler."
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
            apply_letterhead_strip(
                classifications,
                min_repeat_pages=int(
                    getattr(self.config, "LETTERHEAD_REPEAT_MIN_PAGES", 3)
                ),
            )
            self._layouts = load_pdf_layouts(pdf_path)

            native_n = sum(1 for c in classifications if c.page_class == "native")
            hybrid_n = sum(1 for c in classifications if c.page_class == "hybrid")
            image_n = sum(1 for c in classifications if c.page_class == "image_page")
            forced_skipped = sum(
                1
                for c in classifications
                if c.force_image_ocr and c.page_class != "image_page"
            )
            logger.info(
                "Classificação: total=%s native=%s hybrid=%s image_page=%s "
                "(force_ocr ignorado por texto utilizável=%s)",
                len(classifications),
                native_n,
                hybrid_n,
                image_n,
                forced_skipped,
            )
            if progress_callback:
                try:
                    progress_callback(
                        0,
                        len(classifications),
                        {
                            "type": "classification",
                            "total_pages": len(classifications),
                            "native_pages": native_n,
                            "hybrid_pages": hybrid_n,
                            "image_pages": image_n,
                            "force_ocr_skipped": forced_skipped,
                        },
                    )
                except Exception as cb_exc:
                    logger.debug("Progress callback error: %s", cb_exc)

            pages_data: List[Dict] = []
            total = len(classifications)
            batch_size = self._adaptive_ocr_batch_size()
            prefetch = ThreadPoolExecutor(max_workers=1)
            try:
                idx = 0
                while idx < total:
                    classification = classifications[idx]
                    if classification.page_class == "image_page":
                        run_end = idx + 1
                        while (
                            run_end < total
                            and classifications[run_end].page_class == "image_page"
                            and (run_end - idx) < batch_size
                        ):
                            run_end += 1
                        run = classifications[idx:run_end]
                        batch_pages = self._process_image_page_run(
                            pdf_path=pdf_path,
                            processo=processo,
                            classifications=run,
                            page_texts=page_texts,
                            images_dir=images_dir,
                            enable_handwriting=use_handwriting,
                            prefetch=prefetch,
                        )
                        for offset, page_data in enumerate(batch_pages):
                            pages_data.append(page_data)
                            if progress_callback:
                                try:
                                    progress_callback(
                                        run[offset].page, total, page_data
                                    )
                                except Exception as cb_exc:
                                    logger.debug(
                                        "Progress callback error: %s", cb_exc
                                    )
                        idx = run_end
                        continue

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
                                classification.page, total, page_data
                            )
                        except Exception as cb_exc:
                            logger.debug("Progress callback error: %s", cb_exc)
                    idx += 1
            finally:
                prefetch.shutdown(wait=False)

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
                    "ocr_library": self._ocr_library(),
                    "paddle_gpu_fallback": self._paddle_gpu is not None,
                    "paddle_gpu_fallback_alert": self._paddle_gpu_fallback_text(),
                    "paddle_native_cuda_error": (
                        f"{type(self._paddle_native_cuda_error).__name__}: "
                        f"{self._paddle_native_cuda_error}"
                        if self._paddle_native_cuda_error
                        else None
                    ),
                },
            }
        except Exception:
            logger.exception("Error processing PDF: %s", pdf_path.name)
            raise
        finally:
            self._log_paddle_gpu_fallback_alert(
                etapa="fim do processamento",
                include_traceback=True,
            )

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
        png_bytes: Optional[bytes] = None,
        raster_path: Optional[Path] = None,
        ocr_tuple=None,
        prior_elapsed: float = 0.0,
    ) -> Dict:
        page_num = classification.page
        page_class = classification.page_class
        subtype = classification.subtype
        fls = classification.stamp.fls
        stamp = classification.stamp
        sig_ids = list(stamp.signature_ids or extract_signature_ids(raw_text or ""))

        device, library = self._device_library_for_class(page_class)

        extra = max(0.0, float(prior_elapsed or 0.0))
        t0 = time.time() - extra
        inicio_iso = datetime.fromtimestamp(t0).isoformat(timespec="seconds")

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
                        png_bytes=png_bytes,
                        raster_path=raster_path,
                        ocr_tuple=ocr_tuple,
                    )
                )
                page_data["device"] = self._ocr_device()
                page_data["library"] = self._ocr_library()
                page_data["fls"] = classification.stamp.fls
                page_data["marker_page_number"] = classification.stamp.fls
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
        image_paths: List[str] = []
        figure_entries: List[Tuple[float, float, str, str]] = []

        saved = extract_page_images_to_dir(
            pdf_path,
            classification.page,
            images_dir,
            prefix="hybrid",
            skip_logos=True,
            page_images=classification.images,
        )
        layout = (self._layouts or {}).get(classification.page)
        info_by_num = {img.num: img for img in (classification.images or [])}

        for path in saved:
            if path.stat().st_size < 3000:
                continue
            info = self._image_info_for_path(path, info_by_num)
            if info is not None and (
                info.is_logo_or_icon() or info.is_banner_or_footer_image()
            ):
                continue
            image_paths.append(str(path))
            try:
                ocr_text, has_tables, _lang, table_mds = self._ocr_image_file(
                    path, prefer_tables=True, dpi_hint=300
                )
            except Exception as exc:
                logger.warning(
                    "Hybrid figure OCR failed page %s (%s): %s",
                    classification.page,
                    path.name,
                    exc,
                )
                continue
            if not (ocr_text or "").strip():
                continue
            if is_duplicate_figure_text(ocr_text, body):
                continue
            fig_rect = None
            if info is not None and info.y is not None:
                fig_rect = LayoutRect(
                    x=float(info.x or 0),
                    y=float(info.y),
                    width=float(info.display_width or info.width or 0),
                    height=float(info.display_height or info.height or 0),
                )
            if fig_rect is not None and native_text_covers_rect(layout, fig_rect):
                continue
            formatted = format_labor_document(
                ocr_text,
                detect_doc_kind_from_text(ocr_text),
                tables_markdown=table_mds or None,
            )
            y = float(info.y) if info is not None and info.y is not None else 10_000.0
            x = float(info.x) if info is not None and info.x is not None else 0.0
            figure_entries.append((y, x, formatted, str(path)))

        if figure_entries and any(y < 9_000 for y, _x, _f, _p in figure_entries):
            text = self._merge_body_with_figures(body, layout, figure_entries)
        else:
            text = self._append_unique_figures(body, figure_entries)

        use_gpu = is_gpu_mode(self.mode)
        return {
            "text": (text or body).strip(),
            "residual_text": classification.residual_text,
            "type": "hybrid",
            "device": "GPU" if use_gpu else "CPU",
            "library": f"poppler.pdftotext+{self._ocr_library()}",
            "images": image_paths if self.config.EMBED_IMAGES_IN_MD else [],
            "has_tables": any("|" in (fig or "") for _y, _x, fig, _p in figure_entries),
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
        png_bytes: Optional[bytes] = None,
        raster_path: Optional[Path] = None,
        ocr_tuple=None,
    ) -> Dict:
        kind = classification.doc_kind
        dpi = self._dpi_for_kind(kind, classification.subtype)

        if png_bytes is None or raster_path is None:
            png_bytes, raster_path = self._render_classified_raster(
                pdf_path, classification, images_dir, dpi=dpi
            )
        else:
            png_bytes = png_bytes
            raster_path = Path(raster_path)

        ocr_text = ""
        has_tables = False
        language = None
        table_mds: List[str] = []
        ocr_failed = False
        failure_reason = None

        prefer_tables = bool(kind in FORM_KINDS or classification.force_image_ocr)

        if ocr_tuple is not None:
            ocr_text, has_tables, language, table_mds = ocr_tuple
            if not (ocr_text or "").strip() and not table_mds:
                ocr_tuple = None
        if ocr_tuple is None:
            try:
                ocr_text, has_tables, language, table_mds = self._ocr_png_bytes(
                    png_bytes,
                    prefer_tables=prefer_tables,
                    form_kind=kind,
                )
            except Exception as exc:
                error_text = str(exc).lower()
                if self._try_downgrade_ocr_backend(error_text, classification.page):
                    try:
                        ocr_text, has_tables, language, table_mds = self._ocr_png_bytes(
                            png_bytes,
                            prefer_tables=True,
                            form_kind=kind,
                            force_tesseract=self.mode_config.get("backend") == "tesseract",
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
        ocr_folio = prefer_process_folio(
            collect_fls_numbers(ocr_text) + collect_fls_numbers(body)
        )
        if ocr_folio and (
            classification.stamp.fls is None or ocr_folio >= classification.stamp.fls
        ):
            classification.stamp.fls = ocr_folio
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
        force_tesseract = prefer_tables and self.mode_config.get("backend") == "tesseract"
        return self._ocr_png_bytes(
            data,
            prefer_tables=prefer_tables,
            mime_type=mime,
            force_tesseract=force_tesseract,
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
        """Run OCR on image bytes."""
        if (
            self._paddle_gpu is not None
            and not force_tesseract
            and self.mode == ProcessingMode.PADDLE_GPU
        ):
            return self._paddle_gpu.ocr_png_bytes(png_bytes)

        extraction_config, easyocr_kwargs = self._build_extraction_config(
            force_ocr=True,
            prefer_tables=prefer_tables or bool(form_kind in FORM_KINDS),
            force_tesseract=force_tesseract,
        )
        kwargs: dict[str, Any] = {"config": extraction_config}
        if easyocr_kwargs is not None and not force_tesseract:
            kwargs["easyocr_kwargs"] = easyocr_kwargs

        result = kreuzberg.extract_bytes_sync(png_bytes, mime_type, **kwargs)
        return self._ocr_result_tuple(result)

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
            paddle_kwargs = {
                "language": language,
                "enable_table_detection": prefer_tables
                or self.mode_config.get("detect_tables", True),
            }
            model_tier = self.mode_config.get("model_tier")
            if model_tier:
                paddle_kwargs["model_tier"] = model_tier
            padding = self.mode_config.get("padding")
            if padding is not None:
                paddle_kwargs["padding"] = int(padding)
            rec_batch = self._adaptive_rec_batch_num()
            if rec_batch:
                paddle_kwargs["rec_batch_num"] = rec_batch
            det_limit = self.mode_config.get("det_limit_side_len")
            if det_limit:
                # PaddleOcrConfig has no det_limit_type; native Kreuzberg path
                # only gets det_limit_side_len (see GPU_SETUP.md).
                paddle_kwargs["det_limit_side_len"] = int(det_limit)
            try:
                paddle_cfg = kreuzberg.PaddleOcrConfig(**paddle_kwargs)
            except TypeError:
                paddle_cfg = kreuzberg.PaddleOcrConfig(
                    language=language,
                    enable_table_detection=paddle_kwargs["enable_table_detection"],
                    model_tier=model_tier,
                    padding=int(padding) if padding is not None else 10,
                )
            ocr_config = kreuzberg.OcrConfig(
                backend="paddleocr",
                language=language,
                paddle_ocr_config=paddle_cfg,
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
        if backend == "paddleocr":
            if use_gpu and self.mode == ProcessingMode.PADDLE_GPU:
                acceleration = kreuzberg.AccelerationConfig(
                    provider="cuda",
                    device_id=self._cuda_device_id(),
                )
            else:
                acceleration = kreuzberg.AccelerationConfig(provider="cpu")
        elif use_gpu and backend == "easyocr":
            acceleration = kreuzberg.AccelerationConfig(
                provider="cuda",
                device_id=self._cuda_device_id(),
            )

        # LayoutDetectionConfig / EmbeddingConfig exist in Kreuzberg and accept
        # `acceleration`, but this pipeline does not enable layout detection or
        # embeddings — only ExtractionConfig.acceleration is set (PaddleOCR).

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
            device = "GPU" if is_gpu_mode(self.mode) else "CPU"
            return device, f"poppler.pdftotext+{self._ocr_library()}"
        return self._ocr_device(), self._ocr_library()

    def _ocr_device(self) -> str:
        if is_gpu_mode(self.mode) and self.mode_config.get("use_gpu"):
            return "GPU"
        return "CPU"

    def _ocr_library(self, force_tesseract: bool = False) -> str:
        if force_tesseract:
            return "kreuzberg+tesseract"
        backend = self.mode_config.get("backend", "tesseract")
        if backend == "easyocr":
            return "kreuzberg+easyocr"
        if backend == "paddleocr":
            if self.mode == ProcessingMode.PADDLE_GPU:
                if self._paddle_gpu is not None:
                    return "paddleocr+onnxruntime+cuda"
                return "kreuzberg+paddleocr+cuda"
            return "kreuzberg+paddleocr"
        return "kreuzberg+tesseract"

    def _init_paddle_gpu(self) -> None:
        """Ensure Kreuzberg native PaddleOCR runs on CUDA; GPU is mandatory."""
        from utils.ort_runtime import prepare_paddle_gpu_runtime

        if not prepare_paddle_gpu_runtime():
            raise RuntimeError(
                "PaddleOCR GPU falhou: onnxruntime-gpu não expõe CUDAExecutionProvider. "
                "Instale com: uv pip install onnxruntime-gpu>=1.27. "
                "Este modo não cai para CPU."
            )
        ok, probe_exc, probe_cfg = self._probe_kreuzberg_paddle_cuda()
        if ok:
            logger.info(
                "Kreuzberg nativo aceitou PaddleOCR CUDA "
                "(AccelerationConfig provider=cuda device_id=%s)",
                self._cuda_device_id(),
            )
            self._paddle_gpu = None
            self._paddle_native_cuda_error = None
            self._paddle_gpu_fallback_lines = []
            return

        # No OfficialPaddleGpuOcr fallback: require the ort-dynamic wheel
        # (scripts/build_kreuzberg_gpu.sh on Linux). Windows lacks that wheel yet.
        self._paddle_native_cuda_error = probe_exc
        self._paddle_gpu_fallback_lines = []
        self._paddle_gpu = None
        raise RuntimeError(
            self._paddle_native_cuda_error_text(probe_exc, probe_cfg)
        ) from probe_exc

    def _probe_kreuzberg_paddle_cuda(
        self,
    ) -> tuple[bool, Optional[BaseException], Optional[object]]:
        """True when Kreuzberg's bundled PaddleOCR accepts AccelerationConfig cuda."""
        global _NATIVE_PADDLE_CUDA_PROBE
        if _NATIVE_PADDLE_CUDA_PROBE is not None:
            return _NATIVE_PADDLE_CUDA_PROBE
        result = self._run_kreuzberg_paddle_cuda_probe()
        _NATIVE_PADDLE_CUDA_PROBE = result
        return result

    def _run_kreuzberg_paddle_cuda_probe(
        self,
    ) -> tuple[bool, Optional[BaseException], Optional[object]]:
        cfg = None
        try:
            from io import BytesIO

            from PIL import Image, ImageDraw

            img = Image.new("RGB", (320, 64), "white")
            ImageDraw.Draw(img).text((8, 20), "PaddleOCR", fill="black")
            buf = BytesIO()
            img.save(buf, format="PNG")
            cfg, _kwargs = self._build_extraction_config(force_ocr=True)
            kreuzberg.extract_bytes_sync(buf.getvalue(), "image/png", config=cfg)
            return True, None, cfg
        except Exception as exc:
            return False, exc, cfg

    def _paddle_native_cuda_error_text(
        self,
        exc: Optional[BaseException],
        probe_cfg=None,
    ) -> str:
        from utils.ort_runtime import ort_runtime_snapshot

        snap = ort_runtime_snapshot()
        acc = getattr(probe_cfg, "acceleration", None) if probe_cfg is not None else None
        ocr = getattr(probe_cfg, "ocr", None) if probe_cfg is not None else None
        paddle = getattr(ocr, "paddle_ocr_config", None) if ocr is not None else None
        kreuzberg_ver = getattr(kreuzberg, "__version__", "desconhecida")
        err_text = str(exc or "")
        bundled_hint = (
            "sim (mensagem típica do wheel com feature ort-bundled)"
            if "not available in the loaded ONNX Runtime" in err_text
            or "ORT_DYLIB_PATH" in err_text
            else "indeterminado — ver traceback"
        )
        lines = [
            "PaddleOCR GPU recusado: o Kreuzberg nativo não aceitou CUDA.",
            "Este modo não usa fallback — corrija o ambiente ou use PaddleOCR CPU / EasyOCR GPU.",
            "",
            f"Exceção: {type(exc).__name__ if exc else 'desconhecida'}: {exc}",
            f"Kreuzberg: {kreuzberg_ver}",
            f"AccelerationConfig.provider: {getattr(acc, 'provider', '-')}",
            f"AccelerationConfig.device_id: {getattr(acc, 'device_id', '-')}",
            f"PaddleOcrConfig.model_tier: {getattr(paddle, 'model_tier', '-')}",
            f"PaddleOcrConfig.padding: {getattr(paddle, 'padding', '-')}",
            f"Python onnxruntime-gpu: {snap.get('onnxruntime_version') or '-'}",
            f"ORT providers: {snap.get('onnxruntime_providers') or '-'}",
            f"ORT_DYLIB_PATH: {snap.get('ORT_DYLIB_PATH') or '-'}",
            f"ORT capi: {snap.get('capi_dir') or '-'}",
            f"ORT CUDA library: {snap.get('cuda_provider_library') or '-'}",
            f"Plataforma: {snap.get('platform') or '-'}",
            f"ORT empacotado no Kreuzberg (ort-bundled): {bundled_hint}",
            "",
            "Requisitos: wheel do Kreuzberg compilado com ort-dynamic "
            "(scripts/build_kreuzberg_gpu.sh no Linux), onnxruntime-gpu instalado "
            "e ORT_DYLIB_PATH configurado antes de importar kreuzberg "
            "(https://docs.kreuzberg.dev/reference/environment-variables/#ort_dylib_path).",
            "No Windows o wheel ort-dynamic ainda não está disponível — use EasyOCR GPU.",
        ]
        return "\n".join(lines)

    def _paddle_gpu_fallback_text(self) -> str:
        """Kept for UI compatibility; native path no longer uses a GPU fallback."""
        if not self._paddle_gpu_fallback_lines:
            return ""
        return "\n".join(self._paddle_gpu_fallback_lines)

    def _log_paddle_gpu_fallback_alert(
        self, *, etapa: str, include_traceback: bool = False
    ) -> None:
        """No-op: PaddleOCR GPU either runs natively or fails at init."""
        return

    def _check_vlm_endpoint(self) -> None:
        """
        Probe the VLM once per run and disable the fallback if it is down.

        Otherwise every page that misses a template pays a failed connection and
        logs the same outage.
        """
        from core.vlm_ocr import reset_vlm_health, vlm_available

        reset_vlm_health()
        if vlm_available(
            base_url=self.vlm_base_url or None,
            model=self.vlm_model or None,
        ):
            return
        self.enable_vlm_fallback = False

    def _cuda_device_id(self) -> int:
        try:
            return int(gpu_detector.device_id)
        except Exception:
            try:
                return int(getattr(self.config, "CUDA_DEVICE_ID", 0) or 0)
            except (TypeError, ValueError):
                return 0

    def _vram_gb(self) -> float:
        try:
            return float(gpu_detector.get_gpu_info().get("vram_gb") or 0)
        except Exception:
            return 0.0

    def _adaptive_ocr_batch_size(self) -> int:
        configured = int(self.mode_config.get("batch_size") or 1)
        if not is_gpu_mode(self.mode):
            return 1
        vram = self._vram_gb()
        threshold = float(getattr(self.config, "GPU_BATCH_VRAM_GB", 8))
        if vram < threshold:
            return min(configured, 2)
        return max(1, configured)

    def _adaptive_rec_batch_num(self) -> int:
        configured = int(self.mode_config.get("rec_batch_num") or 6)
        if not self.mode_config.get("use_gpu"):
            return configured
        vram = self._vram_gb()
        high = float(getattr(self.config, "GPU_HIGH_VRAM_GB", 12))
        mid = float(getattr(self.config, "GPU_BATCH_VRAM_GB", 8))
        if vram < mid:
            return min(configured, 4)
        if vram < high:
            return min(configured, 8)
        return configured

    def _try_downgrade_ocr_backend(self, error_text: str, page: int) -> bool:
        err = (error_text or "").lower()
        if self.mode == ProcessingMode.GPU and "easyocr" in err:
            logger.warning(
                "GPU OCR failed on page %s (%s). Falling back to CPU.",
                page,
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
            return True
        return False

    def _image_info_for_path(self, path: Path, info_by_num: Dict):
        from core.page_classifier import _image_index_from_name

        idx = _image_index_from_name(path)
        if idx is None:
            return None
        return info_by_num.get(idx) or info_by_num.get(idx + 1)

    def _append_unique_figures(
        self,
        body: str,
        figure_entries: Sequence[Tuple[float, float, str, str]],
    ) -> str:
        parts = [body] if body else []
        for _y, _x, fig, _path in figure_entries:
            if fig and not is_duplicate_figure_text(fig, body):
                parts.append(fig)
        return "\n\n".join(p for p in parts if p).strip()

    def _merge_body_with_figures(
        self,
        body: str,
        layout,
        figure_entries: Sequence[Tuple[float, float, str, str]],
    ) -> str:
        figures = sorted(
            [(y, x, fig) for y, x, fig, _p in figure_entries if fig.strip()],
            key=lambda t: (t[0], t[1]),
        )
        if not figures:
            return body
        if not body:
            return "\n\n".join(fig for _y, _x, fig in figures)

        page_h = float(getattr(layout, "height", 0) or 842)
        lines = body.splitlines()
        n = max(len(lines), 1)

        def line_y(index: int) -> float:
            return page_h * (0.08 + 0.84 * ((index + 0.5) / n))

        parts: List[str] = []
        buf: List[str] = []
        fig_i = 0
        for i, line in enumerate(lines):
            y = line_y(i)
            while fig_i < len(figures) and figures[fig_i][0] <= y:
                if buf:
                    parts.append("\n".join(buf).rstrip())
                    buf = []
                if not is_duplicate_figure_text(figures[fig_i][2], body):
                    parts.append(figures[fig_i][2])
                fig_i += 1
            buf.append(line)
        if buf:
            parts.append("\n".join(buf).rstrip())
        while fig_i < len(figures):
            if not is_duplicate_figure_text(figures[fig_i][2], body):
                parts.append(figures[fig_i][2])
            fig_i += 1
        merged = "\n\n".join(p for p in parts if p and p.strip())
        return re.sub(r"\n{3,}", "\n\n", merged).strip()

    def _render_classified_raster(
        self,
        pdf_path: Path,
        classification,
        images_dir: Path,
        *,
        dpi: Optional[int] = None,
    ) -> Tuple[bytes, Path]:
        dpi = dpi or self._dpi_for_kind(classification.doc_kind, classification.subtype)
        page_index = classification.page - 1
        try:
            png_bytes = kreuzberg.render_pdf_page(str(pdf_path), page_index, dpi=dpi)
        except Exception as exc:
            raise RuntimeError(f"render_pdf_page failed: {exc}") from exc
        raster_path = images_dir / f"raster_page_{classification.page:04d}.png"
        raster_path.write_bytes(png_bytes)
        return png_bytes, raster_path

    def _ocr_result_tuple(self, result) -> Tuple[str, bool, Optional[str], List[str]]:
        text = ""
        if hasattr(result, "content") and result.content:
            text = str(result.content)
        elif hasattr(result, "text"):
            text = str(result.text or "")
        tables = getattr(result, "tables", None) or []
        language = getattr(result, "language", None)
        if language is None and hasattr(result, "metadata"):
            meta = result.metadata
            if isinstance(meta, dict):
                language = meta.get("language")
        table_mds: List[str] = []
        for table in tables:
            md = getattr(table, "markdown", None) or getattr(table, "text", None)
            if md:
                table_mds.append(str(md))
        return text.strip(), bool(tables), language, table_mds

    def _ocr_png_batch(
        self,
        png_list: Sequence[bytes],
        classifications: Sequence,
    ) -> Optional[List[Tuple[str, bool, Optional[str], List[str]]]]:
        if len(png_list) < 2 or not hasattr(kreuzberg, "batch_extract_bytes_sync"):
            return None
        prefer_tables = any(
            (c.doc_kind in FORM_KINDS) or c.force_image_ocr for c in classifications
        )
        extraction_config, easyocr_kwargs = self._build_extraction_config(
            force_ocr=True,
            prefer_tables=prefer_tables,
        )
        kwargs: dict[str, Any] = {"config": extraction_config}
        if easyocr_kwargs is not None:
            kwargs["easyocr_kwargs"] = easyocr_kwargs
        mime_types = ["image/png"] * len(png_list)
        try:
            results = kreuzberg.batch_extract_bytes_sync(
                list(png_list), mime_types, **kwargs
            )
        except TypeError:
            try:
                results = kreuzberg.batch_extract_bytes_sync(
                    list(png_list), mime_types, config=extraction_config
                )
            except Exception as exc:
                logger.debug("batch_extract_bytes_sync failed: %s", exc)
                return None
        except Exception as exc:
            logger.debug("batch_extract_bytes_sync failed: %s", exc)
            return None
        if not results or len(results) != len(png_list):
            return None
        return [self._ocr_result_tuple(item) for item in results]

    def _page_raw_text(self, page_texts: Sequence[str], page_num: int) -> str:
        idx = page_num - 1
        if 0 <= idx < len(page_texts):
            return page_texts[idx]
        return ""

    def _can_batch_ocr(self, classifications: Sequence) -> bool:
        """True when Kreuzberg can OCR several rasters in one call."""
        if self._paddle_gpu is not None:
            return False
        return len(classifications) >= 2 and hasattr(
            kreuzberg, "batch_extract_bytes_sync"
        )

    def _process_image_page_run(
        self,
        *,
        pdf_path: Path,
        processo: str,
        classifications: Sequence,
        page_texts: Sequence[str],
        images_dir: Path,
        enable_handwriting: bool,
        prefetch: ThreadPoolExecutor,
    ) -> List[Dict]:
        if self._can_batch_ocr(classifications):
            return self._process_image_page_run_batched(
                pdf_path=pdf_path,
                processo=processo,
                classifications=classifications,
                page_texts=page_texts,
                images_dir=images_dir,
                enable_handwriting=enable_handwriting,
                prefetch=prefetch,
            )
        return self._process_image_page_run_pipelined(
            pdf_path=pdf_path,
            processo=processo,
            classifications=classifications,
            page_texts=page_texts,
            images_dir=images_dir,
            enable_handwriting=enable_handwriting,
            prefetch=prefetch,
        )

    def _process_image_page_run_batched(
        self,
        *,
        pdf_path: Path,
        processo: str,
        classifications: Sequence,
        page_texts: Sequence[str],
        images_dir: Path,
        enable_handwriting: bool,
        prefetch: ThreadPoolExecutor,
    ) -> List[Dict]:
        rendered: List[Tuple[bytes, Path]] = []
        render_elapsed: List[float] = []
        next_future = None
        for i, classification in enumerate(classifications):
            t_render = time.time()
            if next_future is not None:
                png_bytes, raster_path = next_future.result()
            else:
                png_bytes, raster_path = self._render_classified_raster(
                    pdf_path, classification, images_dir
                )
            render_elapsed.append(time.time() - t_render)
            rendered.append((png_bytes, raster_path))
            if i + 1 < len(classifications):
                nxt = classifications[i + 1]
                next_future = prefetch.submit(
                    self._render_classified_raster, pdf_path, nxt, images_dir
                )
            else:
                next_future = None

        t_ocr = time.time()
        ocr_results = self._ocr_png_batch(
            [png for png, _path in rendered], classifications
        )
        ocr_elapsed = time.time() - t_ocr
        ocr_share = (
            ocr_elapsed / len(classifications)
            if ocr_results and classifications
            else 0.0
        )

        pages: List[Dict] = []
        for i, classification in enumerate(classifications):
            png_bytes, raster_path = rendered[i]
            ocr_tuple = ocr_results[i] if ocr_results else None
            prior = render_elapsed[i] + (ocr_share if ocr_tuple is not None else 0.0)
            pages.append(
                self._process_classified_page(
                    pdf_path=pdf_path,
                    processo=processo,
                    classification=classification,
                    raw_text=self._page_raw_text(page_texts, classification.page),
                    images_dir=images_dir,
                    enable_handwriting=enable_handwriting,
                    png_bytes=png_bytes,
                    raster_path=raster_path,
                    ocr_tuple=ocr_tuple,
                    prior_elapsed=prior,
                )
            )
        return pages

    def _process_image_page_run_pipelined(
        self,
        *,
        pdf_path: Path,
        processo: str,
        classifications: Sequence,
        page_texts: Sequence[str],
        images_dir: Path,
        enable_handwriting: bool,
        prefetch: ThreadPoolExecutor,
    ) -> List[Dict]:
        """Rasterize the next page while OCR runs on the current one."""
        pages: List[Dict] = []
        next_future = None
        for i, classification in enumerate(classifications):
            t_render = time.time()
            if next_future is not None:
                png_bytes, raster_path = next_future.result()
            else:
                png_bytes, raster_path = self._render_classified_raster(
                    pdf_path, classification, images_dir
                )
            render_elapsed = time.time() - t_render

            if i + 1 < len(classifications):
                nxt = classifications[i + 1]
                next_future = prefetch.submit(
                    self._render_classified_raster, pdf_path, nxt, images_dir
                )
            else:
                next_future = None

            pages.append(
                self._process_classified_page(
                    pdf_path=pdf_path,
                    processo=processo,
                    classification=classification,
                    raw_text=self._page_raw_text(page_texts, classification.page),
                    images_dir=images_dir,
                    enable_handwriting=enable_handwriting,
                    png_bytes=png_bytes,
                    raster_path=raster_path,
                    ocr_tuple=None,
                    prior_elapsed=render_elapsed,
                )
            )
        return pages

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
