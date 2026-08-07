"""
Flet-based User Interface for Intelligent OCR System
Simplified with Kreuzberg integration
"""
import flet as ft
from pathlib import Path
import logging
import time
import threading

from config import Config, ProcessingMode
from utils.gpu_detector import gpu_detector
from core.kreuzberg_engine import KreuzbergOCREngine
from core.handwriting_detector import HandwritingDetector
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
        self.selected_mode = ProcessingMode.EXPRESS
        self.is_processing = False
        
        # GPU info
        self.gpu_info = gpu_detector.get_gpu_info()
        logger.info(f"GPU Status: {self.gpu_info['message']}")

        # Setup UI
        self.setup_ui()
    
    def setup_ui(self):
        """Configure Flet interface"""
        # Page configuration
        self.page.title = self.config.WINDOW_TITLE
        self.page.window.width = self.config.WINDOW_WIDTH
        self.page.window.height = self.config.WINDOW_HEIGHT
        self.page.window.min_width = self.config.WINDOW_WIDTH
        self.page.window.min_height = self.config.WINDOW_HEIGHT
        self.page.theme_mode = self._resolve_theme_mode(self.config.THEME_MODE)
        
        # File pickers (services)
        self.pdf_picker = ft.FilePicker()
        self.folder_picker = ft.FilePicker()
        self.page.services.extend([self.pdf_picker, self.folder_picker])
        
        # UI Components
        self.pdf_path_field = ft.TextField(
            label="Arquivo PDF",
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            hint_text="Clique no botão para selecionar..."
        )
        
        self.output_folder_field = ft.TextField(
            label="Pasta de Destino",
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            hint_text="Clique no botão para selecionar..."
        )
        
        # Processing mode radio buttons
        self.mode_radio = ft.RadioGroup(
            content=ft.Column([
                ft.Container(
                    height=32,
                    alignment=ft.Alignment.CENTER_LEFT,
                    content=ft.Radio(
                        value=ProcessingMode.EXPRESS,
                        label="⚡ Express (Rápido) - Tesseract, processamento mínimo"
                    )
                ),
                ft.Container(
                    height=32,
                    alignment=ft.Alignment.CENTER_LEFT,
                    content=ft.Radio(
                        value=ProcessingMode.CPU,
                        label="💻 CPU (Padrão) - Tesseract com pré-processamento completo"
                    )
                ),
                ft.Container(
                    height=32,
                    alignment=ft.Alignment.CENTER_LEFT,
                    content=ft.Radio(
                        value=ProcessingMode.GPU,
                        label="🚀 GPU (Alta Qualidade) - EasyOCR + TrOCR (só c/ NVIDIA GPU)"
                    )
                )
            ], spacing=6),
            value=ProcessingMode.EXPRESS,
            on_change=self.on_mode_changed
        )
        
        # Options
        self.generate_markdown_check = ft.Checkbox(
            label="Gerar arquivo Markdown",
            value=True
        )
        
        self.enable_handwriting_check = ft.Checkbox(
            label="Detectar texto manuscrito (TrOCR - só modo GPU)",
            value=False
        )
        
        # GPU status
        gpu_status_text = self._get_gpu_status_text()
        gpu_color = ft.Colors.GREEN if self.gpu_info['available'] else ft.Colors.ORANGE
        self.gpu_status = ft.Text(gpu_status_text, size=12, color=gpu_color)
        
        # Progress
        self.progress_bar = ft.ProgressBar(expand=True, visible=False)
        self.progress_text = ft.Text("Aguardando seleção de arquivo...", size=14)
        
        # Log area
        self.log_field = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            text_size=12
        )
        
        # Statistics
        self.stats_field = ft.TextField(
            value="",
            multiline=True,
            read_only=True,
            expand=True,
            border_width=1,
            border_color=ft.Colors.GREY_400,
            text_size=12
        )
        
        # Process button
        self.process_button = ft.ElevatedButton(
            content=ft.Text("🚀 PROCESSAR PDF"),
            on_click=self.on_process_clicked,
            disabled=True,
            width=200,
            height=50,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.BLUE_700,
                color=ft.Colors.WHITE
            )
        )

        # Save log button
        self.save_log_button = ft.ElevatedButton(
            content=ft.Text("💾 Salvar Log"),
            on_click=self.on_save_log_clicked,
            height=32
        )
        
        # Build layout
        self.page.add(
            ft.Container(
                expand=True,
                content=ft.Column([
                    # Title
                    ft.Row([
                        ft.Text(
                            "📄 Sistema Inteligente de OCR para PDFs Judiciais",
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            expand=True
                        ),
                        ft.Text(
                            "Powered by Kreuzberg 🚀",
                            size=14,
                            color=ft.Colors.BLUE_700
                        )
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(height=16),

                    # File selection
                    ft.Container(
                        padding=12,
                        border=ft.Border.all(1, ft.Colors.GREY_400),
                        border_radius=5,
                        bgcolor=ft.Colors.GREY_50,
                        content=ft.Column([
                            ft.Text("📁 Seleção de Arquivos", size=16, weight=ft.FontWeight.BOLD),
                            ft.Row([
                                self.pdf_path_field,
                                ft.ElevatedButton(
                                    content=ft.Text("📂 Selecionar PDF"),
                                    on_click=self.handle_pick_pdf,
                                    icon=ft.Icons.PICTURE_AS_PDF,
                                    width=190
                                )
                            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Row([
                                self.output_folder_field,
                                ft.ElevatedButton(
                                    content=ft.Text("📁 Pasta Destino"),
                                    on_click=self.handle_pick_folder,
                                    icon=ft.Icons.FOLDER,
                                    width=190
                                )
                            ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
                        ], spacing=10)
                    ),

                    # Processing mode + options
                    ft.Row([
                        ft.Container(
                            expand=55,
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.BLUE_50,
                            content=ft.Column([
                                ft.Text("⚙️ Modo de Processamento", size=16, weight=ft.FontWeight.BOLD),
                                self.mode_radio
                            ], spacing=8)
                        ),
                        ft.Container(
                            expand=45,
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.GREEN_50,
                            content=ft.Column([
                                ft.Text("🔧 Opções", size=16, weight=ft.FontWeight.BOLD),
                                ft.Container(
                                    height=32,
                                    alignment=ft.Alignment.CENTER_LEFT,
                                    content=self.generate_markdown_check
                                ),
                                ft.Container(
                                    height=32,
                                    alignment=ft.Alignment.CENTER_LEFT,
                                    content=self.enable_handwriting_check
                                ),
                                ft.Container(
                                    height=32,
                                    alignment=ft.Alignment.CENTER_LEFT,
                                    content=ft.Row([
                                        ft.Container(width=14),
                                        ft.Icon(ft.Icons.COMPUTER, size=16),
                                        ft.Text("Status GPU:", size=12),
                                        self.gpu_status
                                    ])
                                )
                            ], spacing=8)
                        )
                    ], spacing=12),

                    # Log + statistics
                    ft.Row([
                        ft.Container(
                            expand=2,
                            height=240,
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.AMBER_50,
                            content=ft.Column([
                                ft.Text("📋 Log de Processamento", size=16, weight=ft.FontWeight.BOLD),
                                self.log_field,
                                ft.Row(
                                    [self.save_log_button],
                                    alignment=ft.MainAxisAlignment.END
                                )
                            ], spacing=8)
                        ),
                        ft.Container(
                            expand=1,
                            height=240,
                            padding=10,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.TEAL_50,
                            content=ft.Column([
                                ft.Text("📊 Estatísticas", size=16, weight=ft.FontWeight.BOLD),
                                self.stats_field
                            ], spacing=8)
                        )
                    ], spacing=12),

                    ft.Row(
                        [
                            ft.Column(
                                [self.progress_text, self.progress_bar],
                                spacing=4,
                                expand=True
                            ),
                            self.process_button
                        ],
                        alignment=ft.MainAxisAlignment.END,
                        vertical_alignment=ft.CrossAxisAlignment.END
                    )

                ], scroll=ft.ScrollMode.AUTO, spacing=12),
                padding=20
            )
        )
    
    def _get_gpu_status_text(self) -> str:
        """Get GPU status text"""
        if self.gpu_info['available']:
            return f"✅ {self.gpu_info['name']} ({self.gpu_info['vram_gb']}GB VRAM)"
        else:
            return "❌ GPU não detectada - Modo GPU indisponível"

    def _resolve_theme_mode(self, value):
        """Resolve theme mode from config to Flet ThemeMode."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "dark":
                return ft.ThemeMode.DARK
            if normalized == "light":
                return ft.ThemeMode.LIGHT
            return ft.ThemeMode.SYSTEM
        return value
    
    async def handle_pick_pdf(self, e):
        """Open file dialog and select a PDF"""
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
        """Open folder dialog and select output directory"""
        path = await self.folder_picker.get_directory_path()
        if path:
            self.output_folder = path
            self.output_folder_field.value = self.output_folder
            self._update_process_button()
            self.page.update()

    def on_save_log_clicked(self, e):
        """Save log to a text file in the selected output folder"""
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
    
    def on_mode_changed(self, e):
        """Processing mode change callback"""
        self.selected_mode = e.control.value
        
        # Disable GPU mode if not available
        if self.selected_mode == ProcessingMode.GPU and not self.gpu_info['available']:
            self.log_message("⚠️ GPU não disponível. Usando modo CPU.", ft.Colors.ORANGE)
            self.selected_mode = ProcessingMode.CPU
            self.mode_radio.value = ProcessingMode.CPU
        
        # Update handwriting checkbox
        if self.selected_mode != ProcessingMode.GPU:
            self.enable_handwriting_check.value = False
            self.enable_handwriting_check.disabled = True
        else:
            self.enable_handwriting_check.disabled = False
        
        self.page.update()
    
    def _update_process_button(self):
        """Update process button state"""
        self.process_button.disabled = not (self.pdf_path and self.output_folder)

    def _get_mode_suffix(self) -> str:
        """Get a stable suffix for the selected processing mode."""
        if isinstance(self.selected_mode, ProcessingMode):
            return self.selected_mode.value
        return str(self.selected_mode).strip().lower()

    def _get_log_filename(self, timestamp: str) -> str:
        """Build log filename with PDF stem prefix when available."""
        if self.pdf_path:
            return f"{Path(self.pdf_path).stem}_log_conversao_{timestamp}.txt"
        return f"log_conversao_{timestamp}.txt"

    def _describe_page_ocr(self, page_data: dict, backend: str) -> str:
        """Describe OCR method applied for a page."""
        page_type = page_data.get('type', 'unknown')
        if page_type == 'native':
            return "nativa (sem OCR)"
        if page_type == 'scanned':
            return f"OCR {backend}"
        return "tipo desconhecido"
    
    def on_process_clicked(self, e):
        """Process button callback"""
        if self.is_processing:
            return
        
        # Validations
        if not self.pdf_path or not Path(self.pdf_path).exists():
            self.log_message("❌ PDF inválido ou não encontrado", ft.Colors.RED)
            return
        
        if not self.output_folder:
            self.log_message("❌ Pasta de destino não selecionada", ft.Colors.RED)
            return
        
        # Start processing in background thread
        self.is_processing = True
        self.process_button.disabled = True
        self.progress_bar.visible = True
        self.progress_text.value = "Processando PDF com Kreuzberg..."
        self.log_field.value = ""
        self.page.update()
        
        thread = threading.Thread(target=self.process_pdf, daemon=True)
        thread.start()
    
    def process_pdf(self):
        """Process PDF (runs in background thread)"""
        pdf_path = self.pdf_path
        output_folder = self.output_folder
        if not pdf_path or not output_folder:
            self.log_message("❌ PDF ou pasta de destino inválidos", ft.Colors.RED)
            return

        try:
            start_time = time.time()
            
            self.log_message(f"🚀 Iniciando processamento: {Path(pdf_path).name}")
            self.log_message(f"   Modo: {self.selected_mode}")
            self.log_message(f"   Powered by Kreuzberg")
            
            # Create OCR engine
            engine = KreuzbergOCREngine(self.selected_mode, self.config)
            
            # Process PDF with Kreuzberg
            self.update_progress(0.1, "Processando PDF com Kreuzberg...")
            result = engine.process_pdf(pdf_path)

            pages_data = result.get('pages', [])
            total_pages = len(pages_data)
            backend = result.get('metadata', {}).get('backend', 'ocr')

            if total_pages:
                self.update_progress(0.2, f"Processando paginas 0/{total_pages}...")
                for idx, page_data in enumerate(pages_data, start=1):
                    ocr_desc = self._describe_page_ocr(page_data, backend)
                    self.log_message(
                        f"   Pagina {idx}/{total_pages}: {ocr_desc}"
                    )
                    progress = 0.2 + (0.6 * (idx / total_pages))
                    self.update_progress(
                        progress,
                        f"Processando paginas {idx}/{total_pages}..."
                    )
            else:
                self.update_progress(0.7, "Processamento concluido, gerando relatorio...")
            
            # Generate Markdown if requested
            if self.generate_markdown_check.value:
                self.log_message("📝 Gerando arquivo Markdown...")
                
                converter = MarkdownConverter(self.config)
                mode_suffix = self._get_mode_suffix()
                output_filename = f"{Path(pdf_path).stem}_ocr_{mode_suffix}.md"
                output_path = Path(output_folder) / output_filename
                
                md_path = converter.convert_to_markdown(result, str(output_path))
                self.log_message(f"✅ Markdown salvo: {md_path}", ft.Colors.GREEN)
            
            # Display statistics
            total_time = time.time() - start_time
            stats = result['statistics']
            
            self.log_message(
                f"\n🎉 Processamento concluído em {total_time:.2f}s!",
                ft.Colors.GREEN
            )
            
            # Statistics text
            stats_text = (
                f"📊 Estatísticas:\n"
                f"   • Total de páginas: {stats['total_pages']}\n"
                f"   • Páginas nativas: {stats['native_pages']}\n"
                f"   • Páginas escaneadas: {stats['scanned_pages']}\n"
                f"   • Páginas com tabelas: {stats['pages_with_tables']}\n"
                f"   • Total de palavras: {stats['total_words']:,}\n"
                f"   • Velocidade: {stats['processing_speed']}"
            )
            self.update_stats(stats_text)
            
            self.update_progress(1.0, "✅ Concluido!")
            
        except Exception as e:
            logger.exception("Error processing PDF")
            self.log_message(f"❌ Erro: {str(e)}", ft.Colors.RED)
            self.update_progress(0, "❌ Erro no processamento")
        
        finally:
            self.is_processing = False
            self.process_button.disabled = False
            self.page.update()
    
    def log_message(self, message: str, color=None):
        """Add message to log"""
        current = self.log_field.value or ""
        if current:
            self.log_field.value = f"{current}\n{message}"
        else:
            self.log_field.value = message
        self.page.update()
    
    def update_progress(self, value: float, text: str):
        """Update progress bar"""
        self.progress_bar.value = value
        self.progress_text.value = text
        self.page.update()
    
    def update_stats(self, text: str):
        """Update statistics display"""
        self.stats_field.value = text
        self.page.update()


def run_app():
    """Start Flet application"""
    def main(page: ft.Page):
        app = OCRApp(page)
    
    ft.app(target=main)
