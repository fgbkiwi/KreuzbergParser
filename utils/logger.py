"""
Centralized logging system
"""
import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_logger(name: str = None, config=None) -> logging.Logger:
    """
    Configure logger for the system
    
    Args:
        name: Logger name (None for root logger)
        config: Configuration object
    
    Returns:
        Configured logger
    """
    if config is None:
        from config import Config
        config = Config()
    
    # Create log directory
    config.ensure_directories()
    
    # Configure logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.LOG_LEVEL))
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(config.LOG_FORMAT)
    
    # Console handler
    if config.LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # File handler
    if config.LOG_TO_FILE:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = config.LOG_DIR / f"ocr_{timestamp}.log"
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger
