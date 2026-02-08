# GPU Setup Guide (EasyOCR + TrOCR)

This project uses EasyOCR + TrOCR for GPU mode. That means GPU acceleration comes from the
PyTorch CUDA wheel, not from PaddleOCR.

## Quick Checklist

- NVIDIA driver installed and working
- PyTorch CUDA wheel installed (cuXXX that matches your system)
- EasyOCR installed

You do NOT need the CUDA Toolkit (nvcc) unless you are compiling custom CUDA code.

## 1) Verify Driver

```powershell
nvidia-smi
```

You should see your GPU name and a CUDA version (this is the driver max version, not the runtime).

## 2) Install PyTorch with CUDA

Choose the correct cuXXX based on your environment. If unsure, start with cu124.

```powershell
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

Other options:
- CUDA 12.1: https://download.pytorch.org/whl/cu121
- CUDA 11.8: https://download.pytorch.org/whl/cu118
- CPU only: https://download.pytorch.org/whl/cpu

## 3) Install EasyOCR

```powershell
pip install easyocr
```

## 4) Verify GPU in PyTorch

```powershell
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

If this prints your GPU name, GPU mode should work.

## 5) Common Issues

- CUDA not available
  - Reinstall PyTorch with a CUDA wheel (cu118/cu121/cu124)
  - Confirm the venv is active

- GPU detected but OCR still uses CPU
  - EasyOCR uses PyTorch. If PyTorch reports CUDA True, GPU should be used.
  - Very low VRAM (2GB) can cause fallback or slowdowns.

## Notes for Newer GPUs

Newer GPUs usually work fine as long as PyTorch is installed with a recent CUDA wheel.
If you share this repo, recommend users to reinstall only the PyTorch stack with the
CUDA version that matches their system.
