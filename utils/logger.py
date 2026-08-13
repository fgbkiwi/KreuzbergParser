"""
Centralized logging system with per-process audit files.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


_PROCESS_FILE_HANDLER: Optional[logging.Handler] = None
_PROCESS_LOG_PATH: Optional[Path] = None
_SESSION_FILE_HANDLER: Optional[logging.Handler] = None
_SESSION_LOG_PATH: Optional[Path] = None
_OCR_SESSION_TS_RE = re.compile(r"(\d{8}_\d{6})\.log$")


def setup_logger(name: Optional[str] = None, config=None) -> logging.Logger:
    """
    Configure logger for the system.

    Handlers are attached to the *root* logger so modules such as core.* and
    utils.* reach both console and file. Named loggers still work via
    propagation.
    """
    if config is None:
        from config import Config
        config = Config()

    config.ensure_directories()

    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL))

    # Avoid duplicate handlers on re-entry
    if not root.handlers:
        formatter = logging.Formatter(config.LOG_FORMAT)

        if config.LOG_TO_CONSOLE:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
            root.addHandler(console_handler)

        if config.LOG_TO_FILE:
            global _SESSION_FILE_HANDLER, _SESSION_LOG_PATH
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = config.LOG_DIR / f"ocr_{timestamp}.log"
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _SESSION_FILE_HANDLER = file_handler
            _SESSION_LOG_PATH = log_file

    # Return named logger (propagates to root) or root itself
    if name:
        named = logging.getLogger(name)
        named.setLevel(getattr(logging, config.LOG_LEVEL))
        return named
    return root


def attach_process_log_file(
    process_id: str,
    config=None,
    *,
    timestamp: Optional[str] = None,
    suffix: Optional[str] = None,
) -> Path:
    """
    Attach a dedicated file handler:
    logs/{process_id}_{suffix}_{YYYYMMDD_HHMMSS}.log

    Replaces any previous process-specific handler. Returns the log path.
    """
    global _PROCESS_FILE_HANDLER, _PROCESS_LOG_PATH

    if config is None:
        from config import Config
        config = Config()

    config.ensure_directories()
    safe_id = _sanitize_filename(process_id or "documento")
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    extra = f"_{suffix}" if suffix else ""
    log_path = config.LOG_DIR / f"{safe_id}{extra}_{ts}.log"
    retarget_session_log(process_id, suffix=suffix)

    root = logging.getLogger()
    if _PROCESS_FILE_HANDLER is not None:
        root.removeHandler(_PROCESS_FILE_HANDLER)
        try:
            _PROCESS_FILE_HANDLER.close()
        except Exception:
            pass
        _PROCESS_FILE_HANDLER = None

    formatter = logging.Formatter(config.LOG_FORMAT)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    _PROCESS_FILE_HANDLER = handler
    _PROCESS_LOG_PATH = log_path
    logging.getLogger(__name__).info("Process audit log: %s", log_path)
    return log_path


def get_process_log_path() -> Optional[Path]:
    return _PROCESS_LOG_PATH


def get_session_log_path() -> Optional[Path]:
    return _SESSION_LOG_PATH


def retarget_session_log(
    process_id: str,
    *,
    suffix: Optional[str] = None,
) -> Optional[Path]:
    """
    Rename the generic session log when PROCESSAR PDF runs:

    logs/ocr_AAAAMMDD_HHMMSS.log
      → logs/{processo}_ocr_{modo_modelo}_AAAAMMDD_HHMMSS.log
    """
    global _SESSION_FILE_HANDLER, _SESSION_LOG_PATH

    if _SESSION_FILE_HANDLER is None or _SESSION_LOG_PATH is None:
        return _SESSION_LOG_PATH

    old_path = _SESSION_LOG_PATH
    ts_match = _OCR_SESSION_TS_RE.search(old_path.name)
    ts = ts_match.group(1) if ts_match else datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = _sanitize_filename(process_id or "documento")
    extra = f"_{suffix}" if suffix else ""
    new_path = old_path.with_name(f"{safe_id}_ocr{extra}_{ts}.log")
    if new_path == old_path:
        return old_path

    root = logging.getLogger()
    formatter = _SESSION_FILE_HANDLER.formatter
    root.removeHandler(_SESSION_FILE_HANDLER)
    try:
        _SESSION_FILE_HANDLER.flush()
        _SESSION_FILE_HANDLER.close()
    except Exception:
        pass
    _SESSION_FILE_HANDLER = None

    try:
        old_path.rename(new_path)
    except OSError:
        try:
            new_path.write_bytes(old_path.read_bytes())
            old_path.unlink(missing_ok=True)
        except OSError:
            new_path = old_path

    handler = logging.FileHandler(new_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    if formatter is not None:
        handler.setFormatter(formatter)
    root.addHandler(handler)
    _SESSION_FILE_HANDLER = handler
    _SESSION_LOG_PATH = new_path
    logging.getLogger(__name__).info("Session log: %s", new_path)
    return new_path


def detach_process_log_file() -> None:
    """Remove the dedicated process file handler if attached."""
    global _PROCESS_FILE_HANDLER, _PROCESS_LOG_PATH
    root = logging.getLogger()
    if _PROCESS_FILE_HANDLER is not None:
        root.removeHandler(_PROCESS_FILE_HANDLER)
        try:
            _PROCESS_FILE_HANDLER.close()
        except Exception:
            pass
        _PROCESS_FILE_HANDLER = None
        _PROCESS_LOG_PATH = None


def log_page_start(
    logger: logging.Logger,
    *,
    processo: str,
    pagina: int,
    fls: Optional[int],
    device: str,
    library: str,
    tipo: str,
    subtype: str = "",
) -> None:
    """Structured PAGE_START audit line."""
    fls_val = fls if fls is not None else "-"
    extra = f" subtype={subtype}" if subtype else ""
    logger.info(
        "PAGE_START processo=%s pagina=%s fls=%s device=%s library=%s tipo=%s%s",
        processo,
        pagina,
        fls_val,
        device,
        library,
        tipo,
        extra,
    )


def log_page_end(
    logger: logging.Logger,
    *,
    processo: str,
    pagina: int,
    fls: Optional[int],
    device: str,
    library: str,
    tipo: str,
    inicio: str,
    fim: str,
    duracao_s: float,
    chars: int,
    subtype: str = "",
) -> None:
    """Structured PAGE_END audit line."""
    fls_val = fls if fls is not None else "-"
    extra = f" subtype={subtype}" if subtype else ""
    logger.info(
        "PAGE_END processo=%s pagina=%s fls=%s device=%s library=%s tipo=%s "
        "inicio=%s fim=%s duracao_s=%.3f chars=%s%s",
        processo,
        pagina,
        fls_val,
        device,
        library,
        tipo,
        inicio,
        fim,
        duracao_s,
        chars,
        extra,
    )


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", value.strip())
    return cleaned[:180] or "documento"
