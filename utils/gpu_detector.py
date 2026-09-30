"""
GPU detection utility - simplified version

Uses NVML (nvidia-ml-py, shipped with the NVIDIA driver's libnvidia-ml /
nvml.dll) instead of torch, so the app does not need PyTorch just to find the
GPU. NVML enumerates devices in PCI bus order and ignores CUDA_VISIBLE_DEVICES;
CUDA_DEVICE_ID selects the device by that index.
"""
import os
import logging
from typing import Dict, Optional

try:
    import pynvml  # nvidia-ml-py
except ImportError:  # pragma: no cover - dependency missing in a broken install
    pynvml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _env_device_id() -> int:
    raw = (os.environ.get("CUDA_DEVICE_ID") or os.environ.get("CUDA_DEVICE") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _nvml_init() -> bool:
    """Initialize NVML; False when there is no NVIDIA driver/GPU."""
    if pynvml is None:
        logger.debug("nvidia-ml-py not installed; GPU detection disabled")
        return False
    try:
        pynvml.nvmlInit()
        return True
    except pynvml.NVMLError as exc:
        logger.debug("NVML init failed: %s", exc)
        return False


class GPUDetector:
    """Detects and validates GPU for OCR processing"""

    def __init__(self, device_id: Optional[int] = None):
        self.cuda_available = False
        self.device_count = 0
        self._handle = None
        self._name = "N/A"
        self._compute_capability = "N/A"
        requested = _env_device_id() if device_id is None else int(device_id)

        if _nvml_init():
            try:
                self.device_count = int(pynvml.nvmlDeviceGetCount())
            except pynvml.NVMLError as exc:
                logger.debug("nvmlDeviceGetCount failed: %s", exc)
            self.cuda_available = self.device_count > 0

        if self.cuda_available:
            if requested >= self.device_count:
                logger.warning(
                    "CUDA_DEVICE_ID=%s out of range (count=%s); using 0",
                    requested,
                    self.device_count,
                )
                requested = 0
            try:
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(requested)
                name = pynvml.nvmlDeviceGetName(self._handle)
                self._name = name.decode() if isinstance(name, bytes) else str(name)
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(self._handle)
                self._compute_capability = f"{major}.{minor}"
            except pynvml.NVMLError as exc:
                logger.warning("NVML could not query cuda:%s: %s", requested, exc)
                self._handle = None
                self.cuda_available = False
        self.device_id = requested

    def _memory_gb(self) -> tuple[float, float]:
        """(total, free) VRAM in GB for the selected device."""
        info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return info.total / (1024 ** 3), info.free / (1024 ** 3)

    def get_gpu_info(self) -> Dict:
        """Returns detailed GPU information"""
        if not self.cuda_available or self._handle is None:
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

        try:
            vram_gb, vram_free_gb = self._memory_gb()
        except pynvml.NVMLError as exc:
            logger.warning("NVML memory query failed: %s", exc)
            vram_gb, vram_free_gb = 0.0, 0.0

        return {
            'available': True,
            'name': self._name,
            'device_id': self.device_id,
            'device_count': self.device_count,
            'vram_gb': round(vram_gb, 2),
            'vram_free_gb': round(vram_free_gb, 2),
            'compute_capability': self._compute_capability,
            'message': (
                f"✅ {self._name} (cuda:{self.device_id}) "
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
