"""
Utility modules initialization
"""
from .logger import setup_logger
from .gpu_detector import gpu_detector, check_gpu_requirements

__all__ = ['setup_logger', 'gpu_detector', 'check_gpu_requirements']
