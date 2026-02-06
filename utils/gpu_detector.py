"""
GPU detection utility - simplified version
"""
import torch
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class GPUDetector:
    """Detects and validates GPU for OCR processing"""
    
    def __init__(self):
        self.cuda_available = torch.cuda.is_available()
        self.device_count = torch.cuda.device_count() if self.cuda_available else 0
        self.device_properties = None
        
        if self.cuda_available and self.device_count > 0:
            self.device_properties = torch.cuda.get_device_properties(0)
    
    def get_gpu_info(self) -> Dict:
        """Returns detailed GPU information"""
        if not self.cuda_available:
            return {
                'available': False,
                'name': 'N/A',
                'vram_gb': 0,
                'vram_free_gb': 0,
                'compute_capability': 'N/A',
                'message': 'CUDA não disponível. GPU mode não estará disponível.'
            }
        
        total_memory = self.device_properties.total_memory
        vram_gb = total_memory / (1024 ** 3)
        
        # Free memory
        torch.cuda.empty_cache()
        free_memory = torch.cuda.mem_get_info()[0]
        vram_free_gb = free_memory / (1024 ** 3)
        
        compute_capability = f"{self.device_properties.major}.{self.device_properties.minor}"
        
        return {
            'available': True,
            'name': self.device_properties.name,
            'vram_gb': round(vram_gb, 2),
            'vram_free_gb': round(vram_free_gb, 2),
            'compute_capability': compute_capability,
            'message': f'✅ {self.device_properties.name} detectada com {vram_gb:.1f}GB VRAM'
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
        
        return True, f"GPU adequada: {gpu_info['name']} ({vram_gb:.1f}GB VRAM)"
    
    def log_gpu_status(self):
        """Logs GPU status"""
        gpu_info = self.get_gpu_info()
        
        if gpu_info['available']:
            logger.info(f"GPU detectada: {gpu_info['name']}")
            logger.info(f"VRAM total: {gpu_info['vram_gb']:.2f}GB")
            logger.info(f"VRAM livre: {gpu_info['vram_free_gb']:.2f}GB")
            logger.info(f"Compute Capability: {gpu_info['compute_capability']}")
        else:
            logger.warning(gpu_info['message'])


# Global instance
gpu_detector = GPUDetector()


def check_gpu_requirements(min_vram_gb: float = 4.0) -> tuple[bool, str]:
    """Helper function to check GPU requirements"""
    return gpu_detector.is_gpu_suitable(min_vram_gb)
