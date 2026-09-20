"""
Flet-based User Interface for Intelligent OCR System
Simplified with Kreuzberg integration
"""
import asyncio
import flet as ft
from pathlib import Path
import logging
import os
import sys
import time
import threading

from config import (
    Config,
    ProcessingMode,
    is_gpu_mode,
    is_processing_mode_available,
    paddle_gpu_supported_on_platform,
    processing_mode_availability,
)
from utils.gpu_detector import gpu_detector
from utils.flet_ui import is_on_session_loop, session_loop
from utils.logger import detach_process_log_file, log_visible_alert
from core.kreuzberg_engine import KreuzbergOCREngine
from core.markdown_converter import MarkdownConverter

logger = logging.getLogger(__name__)

_UI_FLUSH_INTERVAL_S = 0.3
_MAX_VISIBLE_LOG_LINES = 200
_SPLASH_DURATION_S = 4.0
_SPLASH_GIF_WIDTH = 480
_SPLASH_GIF_HEIGHT = 526
_SPLASH_SIDE_CROP_PX = 1
_SPLASH_DISPLAY_HEIGHT = 560
_SPLASH_CORNER_RADIUS = 28
# Matches utils.early_splash: caption under the GIF's own "Kiwi Down".
_SPLASH_AGUARDE_Y_ALIGN = 0.87
# Windows AppUserModelID so taskbar pins associate with Kiwi Down, not flet.exe.
try:
    from utils.windows_aumid import APP_USER_MODEL_ID as _APP_USER_MODEL_ID
except Exception:  # pragma: no cover - installer layout edge cases
    _APP_USER_MODEL_ID = "KiwiDown.App"


def _assets_dir() -> Path:
    """Locate asset files in dev (``assets/``) and Pynsist (install root).

    Prefer the directory that actually contains ``kiwi_down.gif``, including
    the legacy nested ``assets/assets`` layout from older installers.
    """
    try:
        from utils.early_splash import find_splash_gif

        gif = find_splash_gif()
        if gif is not None:
            return gif.parent
    except Exception:
        pass

    candidates: list[Path] = []
    try:
        import config as cfg_mod

        root = Path(cfg_mod.__file__).resolve().parent
        candidates.extend(
            [
                root / "assets",
                root / "assets" / "assets",
                root,
            ]
        )
    except Exception:
        pass
    # ui/app.py -> repo root (dev) or pkgs parent layout
    here = Path(__file__).resolve().parent
    candidates.extend(
        [
            here.parent / "assets",
            here.parent / "assets" / "assets",
            here.parent.parent / "assets",
            here.parent.parent / "assets" / "assets",
            here.parent,
            here.parent.parent,
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for raw in candidates:
        try:
            path = raw.resolve()
        except OSError:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    for path in unique:
        if path.is_dir() and (path / "kiwi_down.gif").is_file():
            return path
    for path in unique:
        if path.is_dir() and path.name.lower() == "assets":
            return path
    return unique[0] if unique else Path("assets")


def _find_app_icon() -> Path | None:
    """Locate ``KiwiDown.ico`` in dev assets and Pynsist install layouts."""
    names = ("KiwiDown.ico", "kiwi_down.ico")
    candidates: list[Path] = []

    try:
        assets = _assets_dir()
        candidates.extend(assets / name for name in names)
    except Exception:
        pass

    try:
        import config as cfg_mod

        root = Path(cfg_mod.__file__).resolve().parent
        for name in names:
            candidates.extend(
                [
                    root / name,
                    root / "assets" / name,
                ]
            )
    except Exception:
        pass

    here = Path(__file__).resolve().parent
    for name in names:
        candidates.extend(
            [
                here.parent / "assets" / name,
                here.parent / name,
                here.parent.parent / "assets" / name,
                here.parent.parent / name,
            ]
        )

    seen: set[str] = set()
    for raw in candidates:
        try:
            path = raw.resolve()
        except OSError:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def _configure_windows_app_identity() -> None:
    """Ask the Flet desktop client to use our AppUserModelID on Windows.

    Also (re)stamps the Start Menu shortcut so pinning uses the launcher,
    not bare ``flet.exe`` (blank white window on relaunch).
    """
    if sys.platform != "win32":
        return
    try:
        from utils.windows_aumid import ensure_windows_taskbar_identity

        ensure_windows_taskbar_identity(_APP_USER_MODEL_ID)
    except Exception:
        os.environ.setdefault("FLET_APP_USER_MODEL_ID", _APP_USER_MODEL_ID)


def _apply_window_icon(page: ft.Page) -> None:
    """Set the live window/taskbar icon (absolute .ico path required on Windows)."""
    icon = _find_app_icon()
    if icon is None:
        return
    page.window.icon = str(icon)


def _app_version() -> str:
    """Resolve APP_VERSION without re-importing main when already loaded."""
    for mod_name in ("main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "APP_VERSION"):
            return str(mod.APP_VERSION)
    try:
        from main import APP_VERSION

        return str(APP_VERSION)
    except Exception:
        return "0.0.0"


class OCRApp:
    """Flet application for OCR processing"""

    def __init__(self, page: ft.Page):
        self.page = page
        self.config = Config()
        self._loop = session_loop(page)

        # GPU info (before default mode / radios)
        self.gpu_info = gpu_detector.get_gpu_info()
        self.gpu_suitable, self.gpu_suitable_message = gpu_detector.is_gpu_suitable(
            self.config.MIN_VRAM_GB
        )
        logger.info("GPU Status: %s", self.gpu_info["message"])
        if not paddle_gpu_supported_on_platform():
            logger.info(
                "PaddleOCR GPU desabilitado nesta plataforma "
                "(ort-dynamic indisponível no Windows)."
            )

        # State
        self.pdf_queue: list[str] = []
        self.output_folder = None
        self.selected_mode = self._default_processing_mode()
        self.is_processing = False
        self._queue_index = 0
        self._queue_total = 0
        self._log_lines: list[str] = []
        self._log_lock = threading.Lock()
        self._pending_progress: tuple[float, str] | None = None
        self._pending_stats: str | None = None
        self._last_ui_flush = 0.0
        self._flush_handle: asyncio.TimerHandle | None = None

        # Setup UI
        self.setup_ui()

    def _default_processing_mode(self) -> ProcessingMode:
        """Prefer EasyOCR GPU when suitable; never pick unavailable modes."""
        if is_processing_mode_available(
            ProcessingMode.GPU, gpu_suitable=self._gpu_suitable_flag()
        ):
            return ProcessingMode.GPU
        return ProcessingMode.CPU

    def _gpu_suitable_flag(self) -> bool:
        if hasattr(self, "gpu_suitable"):
            return bool(self.gpu_suitable)
        is_suitable, _ = gpu_detector.is_gpu_suitable(self.config.MIN_VRAM_GB)
        return is_suitable

    def _mode_is_available(self, mode: ProcessingMode) -> bool:
        return is_processing_mode_available(
            mode, gpu_suitable=self._gpu_suitable_flag()
        )

    def _mode_unavailable_reason(self, mode: ProcessingMode) -> str:
        _, reason = processing_mode_availability(
            mode, gpu_suitable=self._gpu_suitable_flag()
        )
        return reason

    def _mode_label(self, mode: ProcessingMode, base: str) -> str:
        if self._mode_is_available(mode):
            return base
        if mode == ProcessingMode.PADDLE_GPU and not paddle_gpu_supported_on_platform():
            # Keep short: two-column layout clips long suffixes.
            return f"{base} (só Linux)"
        return f"{base} (sem GPU)"

    def setup_ui(self):
        """Configure Flet interface"""
        self.page.title = self.config.WINDOW_TITLE
        self.page.window.width = self.config.WINDOW_WIDTH
        self.page.window.height = self.config.WINDOW_HEIGHT
        self.page.window.min_width = self.config.WINDOW_WIDTH
        self.page.window.min_height = self.config.WINDOW_MIN_HEIGHT
        self.page.padding = 0
        _apply_window_icon(self.page)
        self.page.theme_mode = self._resolve_theme_mode(self.config.THEME_MODE)

        self.pdf_picker = ft.FilePicker()
        self.folder_picker = ft.FilePicker()
        self.page.services.extend([self.pdf_picker, self.folder_picker])

        self.queue_count_text = ft.Text("0 PDF(s) na fila", size=13)

        self.pdf_queue_list = ft.ListView(
            expand=True,
            spacing=2,
            padding=4,
            auto_scroll=False,
        )

        self.clear_queue_button = ft.ElevatedButton(
            content=ft.Text("Limpar fila"),
            on_click=self.on_clear_queue_clicked,
            icon=ft.Icons.CLEAR_ALL,
            height=36,
            disabled=True,
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
            available = self._mode_is_available(value)
            reason = self._mode_unavailable_reason(value)
            return ft.Container(
                alignment=ft.Alignment.CENTER_LEFT,
                tooltip=reason if not available else None,
                content=ft.Radio(
                    value=value.value,
                    label=self._mode_label(value, label),
                    disabled=not available,
                ),
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
                        spacing=4,
                        tight=True,
                        expand=True,
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
                        spacing=4,
                        tight=True,
                        expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
                expand=True,
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
        if self.gpu_suitable:
            gpu_color = ft.Colors.GREEN
        elif self.gpu_info["available"]:
            gpu_color = ft.Colors.ORANGE
        else:
            gpu_color = ft.Colors.ORANGE
        self.gpu_status = ft.Text(
            gpu_status_text,
            size=12,
            color=gpu_color,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self.progress_bar = ft.ProgressBar(expand=True, visible=False)
        self.progress_text = ft.Text(
            "Aguardando seleção de arquivos...", size=14
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
            content=ft.Text("🚀 PROCESSAR FILA"),
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
                        ft.Container(
                            padding=ft.Padding.symmetric(vertical=2),
                            content=ft.Row(
                                [
                                    ft.Text(
                                        f"Kiwi Down v. {_app_version()} — "
                                        "Sistema Inteligente de OCR para PDFs Judiciais",
                                        size=20,
                                        weight=ft.FontWeight.BOLD,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        "Powered by Kreuzberg 🚀",
                                        size=13,
                                        color=ft.Colors.BLUE_700,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ),
                        ft.Divider(height=8),
                        ft.Container(
                            padding=12,
                            border=ft.Border.all(1, ft.Colors.GREY_400),
                            border_radius=5,
                            bgcolor=ft.Colors.GREY_50,
                            content=ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(
                                                "📁 Seleção de Arquivos",
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                                expand=True,
                                            ),
                                            self.queue_count_text,
                                            ft.ElevatedButton(
                                                content=ft.Text("📂 Selecionar PDFs"),
                                                on_click=self.handle_pick_pdf,
                                                icon=ft.Icons.PICTURE_AS_PDF,
                                                width=190,
                                            ),
                                            self.clear_queue_button,
                                        ],
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        spacing=12,
                                    ),
                                    ft.Container(
                                        height=110,
                                        border=ft.Border.all(1, ft.Colors.GREY_400),
                                        border_radius=4,
                                        bgcolor=ft.Colors.WHITE,
                                        content=self.pdf_queue_list,
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
                                    expand=45,
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
                                    expand=55,
                                    height=self.config.MODE_OPTIONS_PANEL_HEIGHT,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.GREEN_50,
                                    content=ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        "🔧 Opções",
                                                        size=16,
                                                        weight=ft.FontWeight.BOLD,
                                                    ),
                                                    ft.Container(expand=True),
                                                    ft.Icon(
                                                        ft.Icons.COMPUTER, size=16
                                                    ),
                                                    ft.Text("Status GPU:", size=12),
                                                    self.gpu_status,
                                                ],
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                spacing=6,
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
                                    height=self.config.LOG_STATS_PANEL_HEIGHT,
                                    padding=10,
                                    border=ft.Border.all(1, ft.Colors.GREY_400),
                                    border_radius=5,
                                    bgcolor=ft.Colors.AMBER_50,
                                    content=ft.Column(
                                        [
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        "📋 Log de Processamento",
                                                        size=16,
                                                        weight=ft.FontWeight.BOLD,
                                                        expand=True,
                                                    ),
                                                    self.save_log_button,
                                                ],
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                            ),
                                            self.log_field,
                                        ],
                                        spacing=8,
                                    ),
                                ),
                                ft.Container(
                                    expand=1,
                                    height=self.config.LOG_STATS_PANEL_HEIGHT,
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
                    spacing=10,
                ),
                # Menos padding vertical para caber em notebooks e não cobrir
                # o botão "PROCESSAR FILA" com a moldura inferior.
                padding=ft.Padding.only(left=16, top=8, right=16, bottom=6),
            )
        )

    def _get_gpu_status_text(self) -> str:
        if self.gpu_info["available"] and self.gpu_suitable:
            return (
                f"✅ {self.gpu_info['name']} "
                f"(cuda:{self.gpu_info.get('device_id', 0)}, "
                f"{self.gpu_info['vram_gb']}GB VRAM)"
            )
        if self.gpu_info["available"] and not self.gpu_suitable:
            return f"⚠️ {self.gpu_suitable_message} — modos GPU desabilitados"
        return "❌ GPU não detectada — modos GPU desabilitados"

    def _resolve_theme_mode(self, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "dark":
                return ft.ThemeMode.DARK
            if normalized == "light":
                return ft.ThemeMode.LIGHT
            return ft.ThemeMode.SYSTEM
        return value

    def _refresh_queue_list(self):
        self.pdf_queue_list.controls.clear()
        for index, path in enumerate(self.pdf_queue):
            name = Path(path).name
            self.pdf_queue_list.controls.append(
                ft.ListTile(
                    dense=True,
                    title=ft.Text(
                        name,
                        size=12,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    subtitle=ft.Text(
                        path,
                        size=10,
                        color=ft.Colors.GREY_600,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    trailing=ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=16,
                        tooltip="Remover da fila",
                        data=index,
                        on_click=self.on_remove_queue_item,
                        disabled=self.is_processing,
                    ),
                )
            )
        n = len(self.pdf_queue)
        self.queue_count_text.value = f"{n} PDF(s) na fila"
        self.clear_queue_button.disabled = n == 0 or self.is_processing
        self._update_process_button()

    async def handle_pick_pdf(self, e):
        if self.is_processing:
            return
        files = await self.pdf_picker.pick_files(
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
            allow_multiple=True,
        )
        if not files:
            return

        existing = set(self.pdf_queue)
        added = 0
        for f in files:
            if not f.path:
                continue
            path = str(Path(f.path).resolve())
            if path in existing:
                continue
            if not path.lower().endswith(".pdf"):
                continue
            self.pdf_queue.append(path)
            existing.add(path)
            added += 1

        if added:
            self._refresh_queue_list()
            self._patch_page()

    def on_remove_queue_item(self, e):
        if self.is_processing:
            return
        index = e.control.data
        if index is None or index < 0 or index >= len(self.pdf_queue):
            return
        self.pdf_queue.pop(index)
        self._refresh_queue_list()
        self._patch_page()

    def on_clear_queue_clicked(self, e):
        if self.is_processing:
            return
        self.pdf_queue.clear()
        self._refresh_queue_list()
        self._patch_page()

    async def handle_pick_folder(self, e):
        path = await self.folder_picker.get_directory_path()
        if path:
            self.output_folder = path
            self.output_folder_field.value = self.output_folder
            self._update_process_button()
            self._patch_page()

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

        if not self._mode_is_available(self.selected_mode):
            reason = self._mode_unavailable_reason(self.selected_mode)
            fallback = (
                ProcessingMode.PADDLE_CPU
                if self.selected_mode == ProcessingMode.PADDLE_GPU
                else ProcessingMode.CPU
            )
            self.log_message(
                f"⚠️ Modo indisponível ({reason}). Usando {fallback.value}.",
                ft.Colors.ORANGE,
            )
            self.selected_mode = fallback
            self.mode_radio.value = fallback.value

        if not is_gpu_mode(self.selected_mode):
            self.enable_handwriting_check.value = False
            self.enable_handwriting_check.disabled = True
        else:
            self.enable_handwriting_check.disabled = False

        self._patch_page()

    def _update_process_button(self):
        self.process_button.disabled = not (
            bool(self.pdf_queue) and self.output_folder and not self.is_processing
        )

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
        if len(self.pdf_queue) > 1:
            return f"batch_log_conversao_{suffix}_{timestamp}.txt"
        if len(self.pdf_queue) == 1:
            return (
                f"{Path(self.pdf_queue[0]).stem}_log_conversao_{suffix}_{timestamp}.txt"
            )
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

    def _queue_label(self) -> str:
        if self._queue_total <= 0:
            return ""
        return f"PDF {self._queue_index}/{self._queue_total}"

    def on_process_clicked(self, e):
        if self.is_processing:
            return

        if not self.pdf_queue:
            self.log_message("❌ Nenhum PDF na fila", ft.Colors.RED)
            return

        missing = [p for p in self.pdf_queue if not Path(p).exists()]
        if missing:
            self.log_message(
                f"❌ PDF(s) não encontrado(s): {', '.join(Path(p).name for p in missing)}",
                ft.Colors.RED,
            )
            return

        if not self.output_folder:
            self.log_message("❌ Pasta de destino não selecionada", ft.Colors.RED)
            return

        if not self._mode_is_available(self.selected_mode):
            reason = self._mode_unavailable_reason(self.selected_mode)
            self.log_message(
                f"❌ Modo {self.selected_mode.value} indisponível: {reason}",
                ft.Colors.RED,
            )
            return

        self.is_processing = True
        self.process_button.disabled = True
        self.clear_queue_button.disabled = True
        self._refresh_queue_list()
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.progress_text.value = "Iniciando fila de conversão..."
        with self._log_lock:
            self._log_lines.clear()
        self.log_field.value = ""
        self._pending_progress = (0, self.progress_text.value)
        self._pending_stats = None
        self.stats_field.value = "📊 Estatísticas:\n   • Aguardando classificação..."
        self._patch_page()

        thread = threading.Thread(target=self.process_queue, daemon=True)
        thread.start()

    def process_queue(self):
        queue = list(self.pdf_queue)
        output_folder = self.output_folder
        if not queue or not output_folder:
            self.log_message("❌ Fila ou pasta de destino inválidos", ft.Colors.RED)
            self.is_processing = False
            self._update_process_button()
            self.clear_queue_button.disabled = not self.pdf_queue
            self._patch_page()
            return

        self._queue_total = len(queue)
        ok_count = 0
        fail_count = 0
        batch_start = time.time()

        enable_hw = bool(self.enable_handwriting_check.value)
        vlm_backend = self.config.resolve_vlm_backend(self.vlm_dropdown.value)
        vlm_preset = self.config.vlm_preset(vlm_backend)
        enable_vlm = bool(vlm_preset.get("enabled"))

        self.log_message(
            f"🚀 Fila iniciada: {self._queue_total} PDF(s)",
            ft.Colors.BLUE_700,
        )
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

        try:
            for index, pdf_path in enumerate(queue):
                self._queue_index = index + 1
                name = Path(pdf_path).name
                sep = "=" * 60
                self.log_message(
                    f"\n{sep}\n===== {self._queue_label()}: {name} =====\n{sep}",
                    ft.Colors.BLUE_700,
                )
                self.update_progress(
                    index / self._queue_total if self._queue_total else 0,
                    f"{self._queue_label()}: {name} — iniciando...",
                )
                self.stats_field.value = (
                    f"📊 Estatísticas ({self._queue_label()}):\n"
                    f"   • Arquivo: {name}\n"
                    f"   • Aguardando classificação..."
                )
                self._patch_page()

                retarget = index == 0
                try:
                    self.process_one_pdf(
                        engine=engine,
                        pdf_path=pdf_path,
                        output_folder=output_folder,
                        enable_hw=enable_hw,
                        enable_vlm=enable_vlm,
                        vlm_backend=vlm_backend,
                        retarget_session=retarget,
                        queue_index=index,
                    )
                    ok_count += 1
                except Exception as exc:
                    fail_count += 1
                    logger.exception("Error processing PDF in batch: %s", pdf_path)
                    self.log_message(
                        f"❌ Erro em {name}: {exc}",
                        ft.Colors.RED,
                    )
                    self._emit_paddle_gpu_fallback_alert(
                        engine._paddle_gpu_fallback_text()
                    )
                finally:
                    detach_process_log_file()
        finally:
            batch_time = time.time() - batch_start
            self.log_message(
                f"\n🏁 Fila concluída em {batch_time:.2f}s — "
                f"ok={ok_count} falha={fail_count} total={self._queue_total}",
                ft.Colors.GREEN if fail_count == 0 else ft.Colors.ORANGE,
            )
            self.update_stats(
                f"📊 Resumo da fila:\n"
                f"   • Total: {self._queue_total}\n"
                f"   • Sucesso: {ok_count}\n"
                f"   • Falha: {fail_count}\n"
                f"   • Tempo: {batch_time:.2f}s"
            )
            if fail_count == 0:
                self.update_progress(1.0, "✅ Fila concluída!")
            else:
                self.update_progress(
                    1.0,
                    f"⚠️ Fila concluída com {fail_count} falha(s)",
                )

            self.is_processing = False
            self._queue_index = 0
            self._queue_total = 0
            self._refresh_queue_list()
            self._schedule_ui_flush(force=True)

    def process_one_pdf(
        self,
        *,
        engine: KreuzbergOCREngine,
        pdf_path: str,
        output_folder: str,
        enable_hw: bool,
        enable_vlm: bool,
        vlm_backend: str,
        retarget_session: bool,
        queue_index: int,
    ) -> None:
        start_time = time.time()
        name = Path(pdf_path).name
        queue_label = self._queue_label()
        total_pdfs = self._queue_total or 1

        self.log_message(f"🚀 Iniciando processamento: {name}")
        images_dir = Path(output_folder) / f"{Path(pdf_path).stem}_images"

        def on_page_progress(current: int, total: int, page_data: dict):
            if page_data.get("type") == "classification":
                planned = (
                    f"📊 Classificação ({queue_label}):\n"
                    f"   • Arquivo: {name}\n"
                    f"   • Total de páginas: {page_data.get('total_pages', total)}\n"
                    f"   • Páginas nativas: {page_data.get('native_pages', 0)}\n"
                    f"   • Páginas híbridas: {page_data.get('hybrid_pages', 0)}\n"
                    f"   • Páginas OCR: {page_data.get('image_pages', 0)}"
                )
                skipped = int(page_data.get("force_ocr_skipped") or 0)
                if skipped:
                    planned += (
                        f"\n   • Force-OCR dispensado (texto utilizável): {skipped}"
                    )
                self.update_stats(planned)
                self.log_message(
                    f"📋 Classificação: nativas={page_data.get('native_pages', 0)} "
                    f"híbridas={page_data.get('hybrid_pages', 0)} "
                    f"OCR={page_data.get('image_pages', 0)}"
                    + (f" (force dispensado={skipped})" if skipped else "")
                )
                page_frac = 0.05
                global_progress = (queue_index + page_frac) / total_pdfs
                self.update_progress(
                    global_progress,
                    f"{queue_label}: {name} — classificado "
                    f"({page_data.get('image_pages', 0)} OCR)...",
                )
                return

            ocr_desc = self._describe_page_ocr(page_data)
            fls = page_data.get("fls")
            fls_bit = f" (Fls. {fls})" if fls is not None else ""
            self.log_message(
                f"   Página {current}/{total}{fls_bit}: {ocr_desc}"
            )
            page_frac = min((current / total) if total else 0, 0.95)
            global_progress = (queue_index + page_frac) / total_pdfs
            self.update_progress(
                global_progress,
                f"{queue_label}: {name} — página {current}/{total}...",
            )

        self.update_progress(
            queue_index / total_pdfs,
            f"{queue_label}: {name} — extraindo e classificando...",
        )
        result = engine.process_pdf(
            pdf_path,
            progress_callback=on_page_progress,
            enable_handwriting=enable_hw,
            images_output_dir=images_dir,
            enable_vlm=enable_vlm,
            vlm_backend=vlm_backend,
            retarget_session=retarget_session,
        )

        self.log_message("📝 Gerando arquivo Markdown...")
        converter = MarkdownConverter(self.config)
        run_suffix = self._get_run_suffix()
        # Windows não permite ':' em nomes de arquivo; HH:MM:SS → HH-MM-SS.
        dt_suffix = time.strftime("%Y-%m-%d_%H-%M-%S")
        output_filename = f"{Path(pdf_path).stem}_ocr_{run_suffix}_{dt_suffix}.md"
        output_path = Path(output_folder) / output_filename
        md_path = converter.convert_to_markdown(result, str(output_path))
        self.log_message(f"✅ Markdown salvo: {md_path}", ft.Colors.GREEN)

        audit_log = result.get("metadata", {}).get("log_path")
        if audit_log:
            self.log_message(f"🧾 Log de auditoria: {audit_log}")

        total_time = time.time() - start_time
        stats = result["statistics"]

        self.log_message(
            f"🎉 {name} concluído em {total_time:.2f}s!",
            ft.Colors.GREEN,
        )

        stats_text = (
            f"📊 Estatísticas ({queue_label}):\n"
            f"   • Arquivo: {name}\n"
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
        self.update_progress(
            (queue_index + 1) / total_pdfs,
            f"{queue_label}: {name} — concluído",
        )

        self._emit_paddle_gpu_fallback_alert(
            result.get("metadata", {}).get("paddle_gpu_fallback_alert"),
            audit_log=audit_log,
        )

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
        with self._log_lock:
            self._log_lines.append(message)
        self._schedule_ui_flush()

    def update_progress(self, value: float, text: str):
        self._pending_progress = (value, text)
        self._schedule_ui_flush()

    def update_stats(self, text: str):
        self._pending_stats = text
        self._schedule_ui_flush()

    def _patch_page(self, *controls: ft.Control) -> None:
        """Send a patch on the session loop so the Flutter client actually paints."""
        if not is_on_session_loop(self.page):
            self._loop.call_soon_threadsafe(self._patch_page, *controls)
            return
        try:
            if controls:
                self.page.update(*controls)
            else:
                self.page.update()
        except Exception:
            logger.debug("Flet page patch failed", exc_info=True)

    def _schedule_ui_flush(self, force: bool = False) -> None:
        if force:
            if is_on_session_loop(self.page):
                self._flush_ui_on_loop(True)
                return
            self._loop.call_soon_threadsafe(self._flush_ui_on_loop, True)
            return
        self._loop.call_soon_threadsafe(self._arm_flush_on_loop)

    def _arm_flush_on_loop(self) -> None:
        if self._flush_handle is not None:
            return
        wait = _UI_FLUSH_INTERVAL_S - (time.monotonic() - self._last_ui_flush)
        if wait <= 0:
            self._flush_ui_on_loop(False)
            return
        self._flush_handle = self._loop.call_later(
            wait, self._flush_ui_on_loop, False
        )

    def _flush_ui_on_loop(self, force: bool = False) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        self._last_ui_flush = time.monotonic()

        with self._log_lock:
            lines = list(self._log_lines)
        pending_progress = self._pending_progress
        pending_stats = self._pending_stats
        self._pending_stats = None

        visible = lines[-_MAX_VISIBLE_LOG_LINES:]
        self.log_field.value = "\n".join(visible)
        controls: list[ft.Control] = [self.log_field]
        if pending_progress is not None:
            self.progress_bar.value = pending_progress[0]
            self.progress_text.value = pending_progress[1]
            controls.extend([self.progress_bar, self.progress_text])
        if pending_stats is not None:
            self.stats_field.value = pending_stats
            controls.append(self.stats_field)
        if force:
            controls.extend(
                [
                    c
                    for c in (
                        getattr(self, "process_button", None),
                        getattr(self, "clear_queue_button", None),
                        getattr(self, "pdf_queue_list", None),
                        getattr(self, "queue_count_text", None),
                    )
                    if c is not None
                ]
            )

        self._patch_page(*controls)




async def _show_splash(page: ft.Page, duration_s: float = _SPLASH_DURATION_S) -> None:
    """Show animated splash GIF centered for ``duration_s`` seconds.

    Crops the thin dark side borders baked into the asset, clips to rounded
    corners, and overlays only \"Aguarde...\" under the GIF's own
    \"Kiwi Down\" lettering (no duplicate title / white footer band).

    Does **not** dismiss the native early splash — the caller closes it after
    the main UI is ready, so the user never sees a blank Flet window.
    """
    gif_path = None
    try:
        from utils.early_splash import find_splash_gif, splash_debug

        gif_path = find_splash_gif()
        splash_debug(
            "flet splash gif=%s assets_dir=%s exists=%s",
            gif_path,
            _assets_dir(),
            bool(gif_path and gif_path.is_file()),
        )
    except Exception as exc:
        try:
            from utils.early_splash import splash_debug

            splash_debug("flet splash setup error: %s", exc)
        except Exception:
            pass

    content_w = _SPLASH_GIF_WIDTH - (2 * _SPLASH_SIDE_CROP_PX)
    content_h = _SPLASH_GIF_HEIGHT
    display_h = _SPLASH_DISPLAY_HEIGHT
    display_w = int(round(display_h * content_w / content_h))
    scale = display_h / content_h
    img_w = _SPLASH_GIF_WIDTH * scale
    img_h = _SPLASH_GIF_HEIGHT * scale
    offset_x = -_SPLASH_SIDE_CROP_PX * scale

    # Prefer assets-relative name (most reliable with ft.app assets_dir).
    # Fall back to absolute path if the file lives outside assets_dir.
    assets = _assets_dir()
    if gif_path is not None and gif_path.parent.resolve() == assets.resolve():
        image_src = gif_path.name
    elif gif_path is not None:
        image_src = str(gif_path)
    else:
        image_src = "kiwi_down.gif"

    page.controls.clear()
    page.add(
        ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            bgcolor=ft.Colors.WHITE,
            content=ft.Container(
                width=display_w,
                height=display_h,
                border_radius=_SPLASH_CORNER_RADIUS,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Stack(
                    [
                        ft.Container(
                            left=offset_x,
                            width=img_w,
                            height=img_h,
                            content=ft.Image(
                                src=image_src,
                                width=img_w,
                                height=img_h,
                                fit=ft.BoxFit.FILL,
                                gapless_playback=True,
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment(0, _SPLASH_AGUARDE_Y_ALIGN),
                            content=ft.Text(
                                "Aguarde...",
                                size=16,
                                color=ft.Colors.GREY_700,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ),
                    ]
                ),
            ),
        )
    )
    page.update()
    await asyncio.sleep(duration_s)
    page.controls.clear()
    page.update()


def run_app():
    """Start Flet application"""
    from utils.flet_ui import patch_flet_desktop_no_console

    patch_flet_desktop_no_console()
    _configure_windows_app_identity()

    # Keep assets_dir aligned with the resolved GIF location.
    try:
        from utils.early_splash import find_splash_gif, splash_debug

        gif = find_splash_gif()
        assets = str(gif.parent) if gif is not None else str(_assets_dir())
        splash_debug("ft.app assets_dir=%s", assets)
    except Exception:
        assets = str(_assets_dir())

    async def main(page: ft.Page):
        # Start hidden so the default blank "Flet" window never flashes.
        # The native early splash covers the wait; we reveal after UI paint.
        try:
            from utils.early_splash import (
                close_early_splash,
                early_splash_active,
                splash_debug,
            )
        except Exception:
            close_early_splash = lambda: None  # noqa: E731
            early_splash_active = lambda: False  # noqa: E731
            splash_debug = lambda *a, **k: None  # noqa: E731

        page.title = Config.WINDOW_TITLE
        _apply_window_icon(page)
        page.window.width = Config.WINDOW_WIDTH
        page.window.height = Config.WINDOW_HEIGHT
        page.window.min_width = Config.WINDOW_WIDTH
        page.window.min_height = Config.WINDOW_MIN_HEIGHT
        page.padding = 0
        page.window.visible = False
        page.update()

        # Skip the in-window GIF splash when the native splash already ran —
        # otherwise the user stares at a blank Flet chrome after it closes.
        if early_splash_active():
            splash_debug("skipping flet splash; early splash still active")
        else:
            await _show_splash(page)

        OCRApp(page)
        page.update()
        page.window.visible = True
        page.window.focused = True
        page.update()
        close_early_splash()
        try:
            from utils.startup_splash import close_splash

            close_splash()
        except Exception:
            pass

    # Hidden until main() reveals the window with content painted.
    ft.app(target=main, assets_dir=assets, view=ft.AppView.FLET_APP_HIDDEN)
