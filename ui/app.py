"""
Flet-based User Interface for Intelligent OCR System
Simplified with Kreuzberg integration
"""
import flet as ft
from pathlib import Path
import logging
import time
import threading

from config import Config, ProcessingMode, is_gpu_mode
from utils.gpu_detector import gpu_detector
from utils.logger import log_visible_alert
from core.kreuzberg_engine import KreuzbergOCREngine
from core.markdown_converter import MarkdownConverter

logger = logging.getLogger(__name__)


class OCRApp:
    """Flet application for OCR processing"""

    def __init__(self, page: ft.Page):
        self.page = page
        self.config = Config()

        # State
        self.pdf_path = None
        self.output_folder = None
        self.selected_mode = self._default_processing_mode()
        self.is_processing = False

        # GPU info
        self.gpu_info = gpu_detector.get_gpu_info()
        logger.info("GPU Status: %s", self.gpu_info["message"])

        # Setup UI
        self.setup_ui()

    def _default_processing_mode(self) -> ProcessingMode:
        """GPU when suitable, otherwise CPU (not EXPRESS)."""
        is_suitable, _ = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
        if is_suitable:
            return ProcessingMode.GPU
        return ProcessingMode.CPU

    def setup_ui(self):
        """Configure Flet interface"""
        self.page.title = self.config.WINDOW_TITLE
        self.page.window.width = self.config.WINDOW_WIDTH
        self.page.window.height = self.config.WINDOW_HEIGHT
        self.page.window.min_width = self.config.WINDOW_WIDTH
        self.page.window.min_height = self.config.WINDOW_HEIGHT
        self.page.theme_mode = self._resolve_theme_mode(self.config.THEME_MODE)

        self.pdf_picker = ft.FilePicker()
        self.folder_picker = ft.FilePicker()
        self.page.services.extend([self.pdf_picker, self.folder_picker])

        self.pdf_path_field = ft.TextField(
            label="Arquivo PDF",
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            hint_text="Clique no botão para selecionar...",
        )

        self.output_folder_field = ft.TextField(
            label="Pasta de Destino",
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            hint_text="Clique no botão para selecionar...",
        )

        handwriting_disabled = not is_gpu_mode(self.selected_mode)

        def _mode_radio(value: ProcessingMode, label: str) -> ft.Container:
            return ft.Container(
                height=32,
                alignment=ft.Alignment.CENTER_LEFT,
                content=ft.Radio(value=value.value, label=label),
            )

        self.mode_radio = ft.RadioGroup(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            _mode_radio(
                                ProcessingMode.EXPRESS,
                                "⚡ Express - Tesseract 200 DPI",
                            ),
                            _mode_radio(
                                ProcessingMode.CPU,
                                "💻 CPU - Tesseract 300 DPI",
                            ),
                            _mode_radio(
                                ProcessingMode.GPU,
                                "🚀 GPU - EasyOCR CUDA",
                            ),
                        ],
                        spacing=6,
                        tight=True,
                    ),
                    ft.Column(
                        [
                            _mode_radio(
                                ProcessingMode.PADDLE_CPU,
                                "📄 PaddleOCR CPU - 300 DPI",
                            ),
                            _mode_radio(
                                ProcessingMode.PADDLE_GPU,
                                "🚀 PaddleOCR GPU - 300 DPI",
                            ),
                        ],
                        spacing=6,
                        tight=True,
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            value=self.selected_mode.value,
            on_change=self.on_mode_changed,
        )

        self.enable_handwriting_check = ft.Checkbox(
            label="Detectar texto manuscrito (TrOCR - só modo GPU)",
            value=False,
            disabled=handwriting_disabled,
        )

        vlm_default = self.config.resolve_vlm_backend()
        self.vlm_dropdown = ft.Dropdown(
            label="VLM fallback (formulários / tabelas)",
            value=vlm_default,
            options=[
                ft.DropdownOption(key=key, text=preset["label"])
                for key, preset in self.config.VLM_PRESETS.items()
            ],
            dense=True,
            text_size=13,
            on_select=self.on_vlm_backend_changed,
        )

        gpu_status_text = self._get_gpu_status_text()
        gpu_color = (
            ft.Colors.GREEN if self.gpu_info["available"] else ft.Colors.ORANGE
        )
        self.gpu_status = ft.Text(gpu_status_text, size=12, color=gpu_color)

        self.progress_bar = ft.ProgressBar(expand=True, visible=False)
        self.progress_text = ft.Text(
            "Aguardando seleção de arquivo...", size=14
        )

        self.log_field = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            text_size=12,
        )

        self.stats_field = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            text_size=12,
        )

        self.process_button = ft.ElevatedButton(
            content=ft.Text("🚀 PROCESSAR PDF"),
            on_click=self.on_process_clicked,
            disabled=True,
            width=200,
            height=50,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.BLUE_700,
                color=ft.Colors.WHITE,
            ),
        )

        self.save_log_button = ft.ElevatedButton(
            content=ft.Text("💾 Salvar Log"),
            on_click=self.on_save_log_clicked,
            height=32,
        )

        self.page.add(
            ft.Container(
                expand=True,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    "📄 Sistema Inteligente de OCR para PDFs Judiciais",
                                    size=24,
                                    weight=ft.FontWeight.BOLD,
                                    expand=True,
                                ),
                                ft.Text(
                                    "Powered by Kreuzberg 🚀",
                                    size=14,
                                    color=ft.Colors.BLUE_700,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(height=16),
                        ft.Container(
                            padding=12,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.GREY_50,
                            content=ft.Column(
                                [
                                    ft.Text(
                                        "📁 Seleção de Arquivos",
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    ft.Row(
                                        [
                                            self.pdf_path_field,
                                            ft.ElevatedButton(
                                                content=ft.Text("📂 Selecionar PDF"),
                                                on_click=self.handle_pick_pdf,
                                                icon=ft.Icons.PICTURE_AS_PDF,
                                                width=190,
                                            ),
                                        ],
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Row(
                                        [
                                            self.output_folder_field,
                                            ft.ElevatedButton(
                                                content=ft.Text("📁 Pasta Destino"),
                                                on_click=self.handle_pick_folder,
                                                icon=ft.Icons.FOLDER,
                                                width=190,
                                            ),
                                        ],
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                ],
                                spacing=10,
                            ),
                        ),
                        ft.Row(
                            [
                                ft.Container(
                                    expand=55,
                                    height=self.config.MODE_OPTIONS_PANEL_HEIGHT,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.BLUE_50,
                                    content=ft.Column(
                                        [
                                            ft.Text(
                                                "⚙️ Modo de Processamento",
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            self.mode_radio,
                                        ],
                                        spacing=8,
                                        tight=True,
                                    ),
                                ),
                                ft.Container(
                                    expand=45,
                                    height=self.config.MODE_OPTIONS_PANEL_HEIGHT,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.GREEN_50,
                                    content=ft.Column(
                                        [
                                            ft.Text(
                                                "🔧 Opções",
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            ft.Container(
                                                height=32,
                                                alignment=ft.Alignment.CENTER_LEFT,
                                                content=self.enable_handwriting_check,
                                            ),
                                            ft.Container(
                                                height=56,
                                                alignment=ft.Alignment.CENTER_LEFT,
                                                content=self.vlm_dropdown,
                                            ),
                                            ft.Container(
                                                height=32,
                                                alignment=ft.Alignment.CENTER_LEFT,
                                                content=ft.Row(
                                                    [
                                                        ft.Container(width=14),
                                                        ft.Icon(
                                                            ft.Icons.COMPUTER, size=16
                                                        ),
                                                        ft.Text("Status GPU:", size=12),
                                                        self.gpu_status,
                                                    ]
                                                ),
                                            ),
                                        ],
                                        spacing=8,
                                        tight=True,
                                    ),
                                ),
                            ],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        ft.Row(
                            [
                                ft.Container(
                                    expand=2,
                                    height=240,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.AMBER_50,
                                    content=ft.Column(
                                        [
                                            ft.Text(
                                                "📋 Log de Processamento",
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            self.log_field,
                                            ft.Row(
                                                [self.save_log_button],
                                                alignment=ft.MainAxisAlignment.END,
                                            ),
                                        ],
                                        spacing=8,
                                    ),
                                ),
                                ft.Container(
                                    expand=1,
                                    height=240,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.TEAL_50,
                                    content=ft.Column(
                                        [
                                            ft.Text(
                                                "📊 Estatísticas",
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                            self.stats_field,
                                        ],
                                        spacing=8,
                                    ),
                                ),
                            ],
                            spacing=12,
                        ),
                        ft.Row(
                            [
                                ft.Column(
                                    [self.progress_text, self.progress_bar],
                                    spacing=4,
                                    expand=True,
                                ),
                                self.process_button,
                            ],
                            alignment=ft.MainAxisAlignment.END,
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=12,
                ),
                padding=20,
            )
        )

    def _get_gpu_status_text(self) -> str:
        if self.gpu_info["available"]:
            return (
                f"✅ {self.gpu_info['name']} "
                f"(cuda:{self.gpu_info.get('device_id', 0)}, "
                f"{self.gpu_info['vram_gb']}GB VRAM)"
            )
        return "❌ GPU não detectada - Modo GPU indisponível"

    def _resolve_theme_mode(self, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "dark":
                return ft.ThemeMode.DARK
            if normalized == "light":
                return ft.ThemeMode.LIGHT
            return ft.ThemeMode.SYSTEM
        return value

    async def handle_pick_pdf(self, e):
        files = await self.pdf_picker.pick_files(
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
        )
        if files and files[0].path:
            self.pdf_path = files[0].path
            self.pdf_path_field.value = self.pdf_path
            self._update_process_button()
            self.page.update()

    async def handle_pick_folder(self, e):
        path = await self.folder_picker.get_directory_path()
        if path:
            self.output_folder = path
            self.output_folder_field.value = self.output_folder
            self._update_process_button()
            self.page.update()

    def on_save_log_clicked(self, e):
        if not self.output_folder:
            self.log_message("❌ Pasta de destino nao selecionada", ft.Colors.RED)
            return

        log_text = self.log_field.value.strip()
        if not log_text:
            self.log_message("⚠️ Log vazio. Nada para salvar.", ft.Colors.ORANGE)
            return

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_filename = self._get_log_filename(timestamp)
        output_path = Path(self.output_folder) / log_filename

        try:
            output_path.write_text(log_text, encoding="utf-8")
            self.log_message(f"✅ Log salvo: {output_path}", ft.Colors.GREEN)
        except Exception as exc:
            logger.exception("Error saving log")
            self.log_message(f"❌ Erro ao salvar log: {exc}", ft.Colors.RED)

    def on_vlm_backend_changed(self, e):
        preset = self.config.vlm_preset(self.vlm_dropdown.value)
        if preset["key"] == "off":
            self.log_message("VLM fallback desligado (só templates).", ft.Colors.BLUE_700)
            return
        self.log_message(
            f"VLM: {preset['label']} → {preset.get('model') or '-'} "
            f"({preset.get('base_url') or '-'})",
            ft.Colors.BLUE_700,
        )

    def on_mode_changed(self, e):
        raw = getattr(e.control, "value", None) or e.control.value
        try:
            self.selected_mode = ProcessingMode(raw)
        except ValueError:
            self.selected_mode = ProcessingMode.CPU

        if is_gpu_mode(self.selected_mode) and not self.gpu_info["available"]:
            fallback = (
                ProcessingMode.PADDLE_CPU
                if self.selected_mode == ProcessingMode.PADDLE_GPU
                else ProcessingMode.CPU
            )
            self.log_message(
                f"⚠️ GPU não disponível. Usando modo {fallback.value}.",
                ft.Colors.ORANGE,
            )
            self.selected_mode = fallback
            self.mode_radio.value = fallback.value

        if not is_gpu_mode(self.selected_mode):
            self.enable_handwriting_check.value = False
            self.enable_handwriting_check.disabled = True
        else:
            self.enable_handwriting_check.disabled = False

        self.page.update()

    def _update_process_button(self):
        self.process_button.disabled = not (self.pdf_path and self.output_folder)

    def _get_run_suffix(self) -> str:
        backend = self.config.resolve_vlm_backend(self.vlm_dropdown.value)
        preset = self.config.vlm_preset(backend)
        return self.config.run_file_suffix(
            self.selected_mode,
            backend,
            enable_vlm=bool(preset.get("enabled")),
        )

    def _get_log_filename(self, timestamp: str) -> str:
        suffix = self._get_run_suffix()
        if self.pdf_path:
            return f"{Path(self.pdf_path).stem}_log_conversao_{suffix}_{timestamp}.txt"
        return f"log_conversao_{suffix}_{timestamp}.txt"

    def _describe_page_ocr(self, page_data: dict) -> str:
        page_type = page_data.get("type", "unknown")
        library = page_data.get("library", "ocr")
        device = page_data.get("device", "?")
        subtype = page_data.get("subtype") or ""
        duration = page_data.get("processing_time")
        dur = f", {duration:.2f}s" if duration is not None else ""
        subtype_bit = f"/{subtype}" if subtype else ""

        if page_type == "native":
            return f"nativa{subtype_bit} ({library}, {device}{dur})"
        if page_type == "hybrid":
            n_img = len(page_data.get("images") or [])
            return (
                f"híbrida{subtype_bit} ({library}, {device}, "
                f"{n_img} img{dur})"
            )
        if page_type == "image_page":
            status = "FALHA" if page_data.get("ocr_failed") else "OCR"
            src = page_data.get("extraction_source")
            src_bit = f", {src}" if src else ""
            return f"{status}{subtype_bit} ({library}, {device}{src_bit}{dur})"
        return f"tipo={page_type}"

    def on_process_clicked(self, e):
        if self.is_processing:
            return

        if not self.pdf_path or not Path(self.pdf_path).exists():
            self.log_message("❌ PDF inválido ou não encontrado", ft.Colors.RED)
            return

        if not self.output_folder:
            self.log_message("❌ Pasta de destino não selecionada", ft.Colors.RED)
            return

        self.is_processing = True
        self.process_button.disabled = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.progress_text.value = "Classificando páginas e iniciando OCR..."
        self.log_field.value = ""
        self.page.update()

        thread = threading.Thread(target=self.process_pdf, daemon=True)
        thread.start()

    def process_pdf(self):
        pdf_path = self.pdf_path
        output_folder = self.output_folder
        if not pdf_path or not output_folder:
            self.log_message("❌ PDF ou pasta de destino inválidos", ft.Colors.RED)
            return

        engine = None
        try:
            start_time = time.time()
            enable_hw = bool(self.enable_handwriting_check.value)
            vlm_backend = self.config.resolve_vlm_backend(self.vlm_dropdown.value)
            vlm_preset = self.config.vlm_preset(vlm_backend)
            enable_vlm = bool(vlm_preset.get("enabled"))

            self.log_message(f"🚀 Iniciando processamento: {Path(pdf_path).name}")
            self.log_message(f"   Modo: {self.selected_mode}")
            self.log_message(
                f"   TrOCR manuscrito: {'sim' if enable_hw else 'não'}"
            )
            self.log_message(
                f"   VLM fallback: {vlm_preset['label']}"
                + (
                    f" ({vlm_preset.get('model')})"
                    if enable_vlm and vlm_preset.get("model")
                    else ""
                )
            )

            engine = KreuzbergOCREngine(self.selected_mode, self.config)
            if engine._paddle_gpu_fallback_text():
                self.log_message(
                    "⚠️ Kreuzberg nativo recusou CUDA — OCR via PaddleOCR "
                    "oficial na GPU (não é CPU). Detalhes no final do log.",
                    ft.Colors.ORANGE_900,
                )
            images_dir = Path(output_folder) / f"{Path(pdf_path).stem}_images"

            def on_page_progress(current: int, total: int, page_data: dict):
                ocr_desc = self._describe_page_ocr(page_data)
                fls = page_data.get("fls")
                fls_bit = f" (Fls. {fls})" if fls is not None else ""
                self.log_message(
                    f"   Página {current}/{total}{fls_bit}: {ocr_desc}"
                )
                progress = current / total if total else 0
                self.update_progress(
                    min(progress, 0.95),
                    f"Processando página {current}/{total}...",
                )

            self.update_progress(0.02, "Extraindo e classificando páginas...")
            result = engine.process_pdf(
                pdf_path,
                progress_callback=on_page_progress,
                enable_handwriting=enable_hw,
                images_output_dir=images_dir,
                enable_vlm=enable_vlm,
                vlm_backend=vlm_backend,
            )

            self.log_message("📝 Gerando arquivo Markdown...")
            converter = MarkdownConverter(self.config)
            run_suffix = self._get_run_suffix()
            output_filename = f"{Path(pdf_path).stem}_ocr_{run_suffix}.md"
            output_path = Path(output_folder) / output_filename
            md_path = converter.convert_to_markdown(result, str(output_path))
            self.log_message(f"✅ Markdown salvo: {md_path}", ft.Colors.GREEN)

            audit_log = result.get("metadata", {}).get("log_path")
            if audit_log:
                self.log_message(f"🧾 Log de auditoria: {audit_log}")

            total_time = time.time() - start_time
            stats = result["statistics"]

            self.log_message(
                f"\n🎉 Processamento concluído em {total_time:.2f}s!",
                ft.Colors.GREEN,
            )

            stats_text = (
                f"📊 Estatísticas:\n"
                f"   • Total de páginas: {stats['total_pages']}\n"
                f"   • Páginas nativas: {stats.get('native_pages', 0)}\n"
                f"   • Páginas híbridas: {stats.get('hybrid_pages', 0)}\n"
                f"   • Páginas OCR: "
                f"{stats.get('image_pages', stats.get('scanned_pages', 0))}\n"
                f"   • Páginas com tabelas: {stats['pages_with_tables']}\n"
                f"   • Total de palavras: {stats['total_words']:,}\n"
                f"   • Velocidade: {stats['processing_speed']}"
            )
            self.update_stats(stats_text)
            self.update_progress(1.0, "✅ Concluido!")

            self._emit_paddle_gpu_fallback_alert(
                result.get("metadata", {}).get("paddle_gpu_fallback_alert"),
                audit_log=audit_log,
            )

        except Exception as e:
            logger.exception("Error processing PDF")
            self.log_message(f"❌ Erro: {str(e)}", ft.Colors.RED)
            self.update_progress(0, "❌ Erro no processamento")
            if engine is not None:
                self._emit_paddle_gpu_fallback_alert(
                    engine._paddle_gpu_fallback_text()
                )

        finally:
            self.is_processing = False
            self.process_button.disabled = False
            self.page.update()

    def _emit_paddle_gpu_fallback_alert(self, alert, *, audit_log=None) -> None:
        if not alert:
            return
        border = "!" * 78
        self.log_message(f"\n{border}\n{alert}\n{border}", ft.Colors.ORANGE_900)
        if audit_log:
            self.log_message(
                f"Traceback completo do probe nativo: {audit_log}",
                ft.Colors.ORANGE_900,
            )
        log_visible_alert(
            logger,
            ["[fim da sessão]"] + str(alert).splitlines(),
        )

    def log_message(self, message: str, color=None):
        current = self.log_field.value or ""
        if current:
            self.log_field.value = f"{current}\n{message}"
        else:
            self.log_field.value = message
        self.page.update()

    def update_progress(self, value: float, text: str):
        self.progress_bar.value = value
        self.progress_text.value = text
        self.page.update()

    def update_stats(self, text: str):
        self.stats_field.value = text
        self.page.update()


def run_app():
    """Start Flet application"""

    def main(page: ft.Page):
        OCRApp(page)

    ft.app(target=main)
