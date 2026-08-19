"""
GPU detection utility - simplified version
"""
import os
import torch
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _env_device_id() -> int:
    raw = (os.environ.get("CUDA_DEVICE_ID") or os.environ.get("CUDA_DEVICE") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


class GPUDetector:
    """Detects and validates GPU for OCR processing"""

    def __init__(self, device_id: Optional[int] = None):
        self.cuda_available = torch.cuda.is_available()
        self.device_count = torch.cuda.device_count() if self.cuda_available else 0
        requested = _env_device_id() if device_id is None else int(device_id)
        if self.cuda_available and self.device_count > 0:
            if requested >= self.device_count:
                logger.warning(
                    "CUDA_DEVICE_ID=%s out of range (count=%s); using 0",
                    requested,
                    self.device_count,
                )
                requested = 0
            self.device_id = requested
            self.device_properties = torch.cuda.get_device_properties(self.device_id)
        else:
            self.device_id = requested
            self.device_properties = None

    def get_gpu_info(self) -> Dict:
        """Returns detailed GPU information"""
        if not self.cuda_available or self.device_properties is None:
            return {
                'available': False,
                'name': 'N/A',
                'device_id': self.device_id,
                'device_count': self.device_count,
                'vram_gb': 0,
                'vram_free_gb': 0,
                'compute_capability': 'N/A',
                'message': 'CUDA não disponível. GPU mode não estará disponível.'
            }

        total_memory = self.device_properties.total_memory
        vram_gb = total_memory / (1024 ** 3)

        torch.cuda.empty_cache()
        free_memory = torch.cuda.mem_get_info(self.device_id)[0]
        vram_free_gb = free_memory / (1024 ** 3)

        compute_capability = f"{self.device_properties.major}.{self.device_properties.minor}"

        return {
            'available': True,
            'name': self.device_properties.name,
            'device_id': self.device_id,
            'device_count': self.device_count,
            'vram_gb': round(vram_gb, 2),
            'vram_free_gb': round(vram_free_gb, 2),
            'compute_capability': compute_capability,
            'message': (
                f"✅ {self.device_properties.name} (cuda:{self.device_id}) "
                f"com {vram_gb:.1f}GB VRAM"
            )
        }

    def is_gpu_suitable(self, min_vram_gb: float = 4.0) -> tuple[bool, str]:
        """Checks if GPU is suitable for processing"""
        if not self.cuda_available:
            return False, "GPU NVIDIA não detectada"

        gpu_info = self.get_gpu_info()
        vram_gb = gpu_info['vram_gb']

        if vram_gb < min_vram_gb:
            return False, (
                f"VRAM insuficiente: {vram_gb:.1f}GB "
                f"(mínimo: {min_vram_gb:.1f}GB)"
            )

        return True, (
            f"GPU adequada: {gpu_info['name']} "
            f"cuda:{gpu_info['device_id']} ({vram_gb:.1f}GB VRAM)"
        )

    def log_gpu_status(self):
        """Logs GPU status"""
        gpu_info = self.get_gpu_info()

        if gpu_info['available']:
            logger.info("GPU detectada: %s", gpu_info['name'])
            logger.info("CUDA device_id: %s (count=%s)", gpu_info['device_id'], gpu_info['device_count'])
            logger.info("VRAM total: %.2fGB", gpu_info['vram_gb'])
            logger.info("VRAM livre: %.2fGB", gpu_info['vram_free_gb'])
            logger.info("Compute Capability: %s", gpu_info['compute_capability'])
        else:
            logger.warning(gpu_info['message'])


# Global instance
gpu_detector = GPUDetector()


def check_gpu_requirements(min_vram_gb: float = 4.0) -> tuple[bool, str]:
    """Helper function to check GPU requirements"""
    return gpu_detector.is_gpu_suitable(min_vram_gb)
