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
        
        # Setup UI
        self.setup_ui()
        
        # GPU info
        self.gpu_info = gpu_detector.get_gpu_info()
        logger.info(f"GPU Status: {self.gpu_info['message']}")
    
    def setup_ui(self):
        """Configure Flet interface"""
        # Page configuration
        self.page.title = self.config.WINDOW_TITLE
        self.page.window_width = self.config.WINDOW_WIDTH
        self.page.window_height = self.config.WINDOW_HEIGHT
        self.page.theme_mode = self.config.THEME_MODE
        
        # File pickers
        self.pdf_picker = ft.FilePicker(on_result=self.on_pdf_selected)
        self.folder_picker = ft.FilePicker(on_result=self.on_folder_selected)
        
        self.page.overlay.extend([self.pdf_picker, self.folder_picker])
        
        # UI Components
        self.pdf_path_field = ft.TextField(
            label="Arquivo PDF",
            read_only=True,
            width=500,
            hint_text="Clique no botão para selecionar..."
        )
        
        self.output_folder_field = ft.TextField(
            label="Pasta de Destino",
            read_only=True,
            width=500,
            hint_text="Clique no botão para selecionar..."
        )
        
        # Processing mode radio buttons
        self.mode_radio = ft.RadioGroup(
            content=ft.Column([
                ft.Radio(
                    value=ProcessingMode.EXPRESS,
                    label="⚡ Express (Rápido) - Tesseract, processamento mínimo"
                ),
                ft.Radio(
                    value=ProcessingMode.CPU,
                    label="💻 CPU (Padrão) - Tesseract com pré-processamento completo"
                ),
                ft.Radio(
                    value=ProcessingMode.GPU,
                    label="🚀 GPU (Alta Qualidade) - PaddleOCR + TrOCR (requer NVIDIA GPU)"
                )
            ]),
            value=ProcessingMode.EXPRESS,
            on_change=self.on_mode_changed
        )
        
        # Options
        self.generate_markdown_check = ft.Checkbox(
            label="Gerar arquivo Markdown",
            value=True
        )
        
        self.enable_handwriting_check = ft.Checkbox(
            label="Detectar texto manuscrito (TrOCR - apenas modo GPU)",
            value=False
        )
        
        # GPU status
        gpu_status_text = self._get_gpu_status_text()
        gpu_color = ft.colors.GREEN if self.gpu_info['available'] else ft.colors.ORANGE
        self.gpu_status = ft.Text(gpu_status_text, size=12, color=gpu_color)
        
        # Progress
        self.progress_bar = ft.ProgressBar(width=600, visible=False)
        self.progress_text = ft.Text("Aguardando seleção de arquivo...", size=14)
        
        # Log area
        self.log_column = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            height=200,
            width=650
        )
        
        # Statistics
        self.stats_text = ft.Text("", size=12)
        
        # Process button
        self.process_button = ft.ElevatedButton(
            text="🚀 PROCESSAR PDF",
            on_click=self.on_process_clicked,
            disabled=True,
            width=200,
            height=50,
            style=ft.ButtonStyle(
                bgcolor=ft.colors.BLUE_700,
                color=ft.colors.WHITE
            )
        )
        
        # Build layout
        self.page.add(
            ft.Container(
                content=ft.Column([
                    # Title
                    ft.Text(
                        "📄 Sistema Inteligente de OCR para PDFs Judiciais",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER
                    ),
                    ft.Text(
                        "Powered by Kreuzberg 🚀",
                        size=14,
                        color=ft.colors.BLUE_700,
                        text_align=ft.TextAlign.CENTER
                    ),
                    ft.Divider(height=20),
                    
                    # File selection
                    ft.Text("📁 Seleção de Arquivos", size=16, weight=ft.FontWeight.BOLD),
                    ft.Row([
                        ft.ElevatedButton(
                            "📂 Selecionar PDF",
                            on_click=lambda _: self.pdf_picker.pick_files(
                                allowed_extensions=["pdf"]
                            ),
                            icon=ft.icons.PICTURE_AS_PDF
                        ),
                        self.pdf_path_field
                    ]),
                    
                    ft.Row([
                        ft.ElevatedButton(
                            "📁 Pasta Destino",
                            on_click=lambda _: self.folder_picker.get_directory_path(),
                            icon=ft.icons.FOLDER
                        ),
                        self.output_folder_field
                    ]),
                    
                    ft.Divider(height=20),
                    
                    # Processing mode
                    ft.Text("⚙️ Modo de Processamento", size=16, weight=ft.FontWeight.BOLD),
                    self.mode_radio,
                    
                    # Options
                    ft.Text("🔧 Opções", size=16, weight=ft.FontWeight.BOLD),
                    self.generate_markdown_check,
                    self.enable_handwriting_check,
                    
                    # GPU status
                    ft.Row([
                        ft.Icon(ft.icons.COMPUTER, size=16),
                        ft.Text("Status GPU:", size=12),
                        self.gpu_status
                    ]),
                    
                    ft.Divider(height=20),
                    
                    # Process button
                    ft.Container(
                        content=self.process_button,
                        alignment=ft.alignment.center
                    ),
                    
                    # Progress
                    self.progress_bar,
                    self.progress_text,
                    
                    ft.Divider(height=10),
                    
                    # Log
                    ft.Text("📋 Log de Processamento", size=14, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=self.log_column,
                        border=ft.border.all(1, ft.colors.GREY_400),
                        border_radius=5,
                        padding=10
                    ),
                    
                    # Statistics
                    self.stats_text
                    
                ], scroll=ft.ScrollMode.AUTO, spacing=10),
                padding=20
            )
        )
    
    def _get_gpu_status_text(self) -> str:
        """Get GPU status text"""
        if self.gpu_info['available']:
            return f"✅ {self.gpu_info['name']} ({self.gpu_info['vram_gb']}GB VRAM)"
        else:
            return "❌ GPU não detectada - Modo GPU indisponível"
    
    def on_pdf_selected(self, e: ft.FilePickerResultEvent):
        """PDF selection callback"""
        if e.files:
            self.pdf_path = e.files[0].path
            self.pdf_path_field.value = self.pdf_path
            self._update_process_button()
            self.page.update()
    
    def on_folder_selected(self, e: ft.FilePickerResultEvent):
        """Folder selection callback"""
        if e.path:
            self.output_folder = e.path
            self.output_folder_field.value = self.output_folder
            self._update_process_button()
            self.page.update()
    
    def on_mode_changed(self, e):
        """Processing mode change callback"""
        self.selected_mode = e.control.value
        
        # Disable GPU mode if not available
        if self.selected_mode == ProcessingMode.GPU and not self.gpu_info['available']:
            self.log_message("⚠️ GPU não disponível. Usando modo CPU.", ft.colors.ORANGE)
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
    
    def on_process_clicked(self, e):
        """Process button callback"""
        if self.is_processing:
            return
        
        # Validations
        if not self.pdf_path or not Path(self.pdf_path).exists():
            self.log_message("❌ PDF inválido ou não encontrado", ft.colors.RED)
            return
        
        if not self.output_folder:
            self.log_message("❌ Pasta de destino não selecionada", ft.colors.RED)
            return
        
        # Start processing in background thread
        self.is_processing = True
        self.process_button.disabled = True
        self.progress_bar.visible = True
        self.log_column.controls.clear()
        self.page.update()
        
        thread = threading.Thread(target=self.process_pdf, daemon=True)
        thread.start()
    
    def process_pdf(self):
        """Process PDF (runs in background thread)"""
        try:
            start_time = time.time()
            
            self.log_message(f"🚀 Iniciando processamento: {Path(self.pdf_path).name}")
            self.log_message(f"   Modo: {self.selected_mode}")
            self.log_message(f"   Powered by Kreuzberg")
            
            # Create OCR engine
            engine = KreuzbergOCREngine(self.selected_mode, self.config)
            
            # Process PDF with Kreuzberg
            self.update_progress(0.3, "Processando PDF com Kreuzberg...")
            result = engine.process_pdf(self.pdf_path)
            
            self.update_progress(0.7, "Processamento concluído, gerando relatório...")
            
            # Generate Markdown if requested
            if self.generate_markdown_check.value:
                self.log_message("📝 Gerando arquivo Markdown...")
                
                converter = MarkdownConverter(self.config)
                output_filename = f"{Path(self.pdf_path).stem}_ocr.md"
                output_path = Path(self.output_folder) / output_filename
                
                md_path = converter.convert_to_markdown(result, str(output_path))
                self.log_message(f"✅ Markdown salvo: {md_path}", ft.colors.GREEN)
            
            # Display statistics
            total_time = time.time() - start_time
            stats = result['statistics']
            
            self.log_message(
                f"\n🎉 Processamento concluído em {total_time:.2f}s!",
                ft.colors.GREEN
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
            
            self.update_progress(1.0, "✅ Concluído!")
            
        except Exception as e:
            logger.exception("Error processing PDF")
            self.log_message(f"❌ Erro: {str(e)}", ft.colors.RED)
            self.update_progress(0, "❌ Erro no processamento")
        
        finally:
            self.is_processing = False
            self.process_button.disabled = False
            self.page.update()
    
    def log_message(self, message: str, color=None):
        """Add message to log"""
        self.log_column.controls.append(
            ft.Text(message, size=12, color=color)
        )
        # Auto-scroll to bottom
        if len(self.log_column.controls) > 20:
            self.log_column.controls.pop(0)
        self.page.update()
    
    def update_progress(self, value: float, text: str):
        """Update progress bar"""
        self.progress_bar.value = value
        self.progress_text.value = text
        self.page.update()
    
    def update_stats(self, text: str):
        """Update statistics display"""
        self.stats_text.value = text
        self.page.update()


def run_app():
    """Start Flet application"""
    def main(page: ft.Page):
        app = OCRApp(page)
    
    ft.app(target=main)
