# CUDA Setup Guide for NVIDIA GeForce GTX 860M

This guide documents the successful setup process for enabling CUDA GPU acceleration on a **NVIDIA GeForce GTX 860M** (Maxwell architecture, 2GB VRAM) with PyTorch on Windows 11.

## Hardware Specifications

- **GPU**: NVIDIA GeForce GTX 860M
- **Architecture**: Maxwell (Compute Capability 5.0)
- **VRAM**: 2GB
- **OS**: Windows 11

---

## Prerequisites

### 1. NVIDIA Driver
- **Required**: Modern NVIDIA driver (400+ series)
- **Tested with**: Driver version 581.80
- **CUDA Version reported**: 13.0

**Verify driver installation:**
```powershell
nvidia-smi
```

Expected output should show:
- Driver version
- CUDA version
- GPU name (NVIDIA GeForce GTX 860M)
- Memory info (2048 MiB)

**Important Notes:**
- You do **NOT** need to downgrade to old drivers (472.xx) for Maxwell GPUs
- Modern drivers (500+/600+) work fine with older GPUs
- Driver CUDA version is just maximum supported; PyTorch bundles its own CUDA runtime

### 2. Do NOT Install CUDA Toolkit Separately
- PyTorch wheels include their own CUDA runtime
- System-wide CUDA toolkit (`nvcc`) is **not required** for PyTorch
- Installing CUDA toolkit can cause conflicts

---

## Current OCR GPU Setup (EasyOCR)

This project now uses **EasyOCR + TrOCR** for GPU mode on Windows. The PaddleOCR GPU backend was removed because PaddleOCR 3.x expects PaddlePaddle 3.x features that are not available in the Windows GPU wheels (only PaddlePaddle 2.6.x GPU builds are published). This caused runtime errors during OCR initialization.

**What this means:**
- GPU mode relies on **PyTorch CUDA** (EasyOCR and TrOCR).
- You still do **not** need the CUDA Toolkit.
- Ensure EasyOCR is installed and PaddleOCR packages are removed.

**Recommended commands (with venv activated):**
```powershell
pip install easyocr
pip uninstall -y paddleocr paddlepaddle paddlepaddle-gpu paddlex
```

---

## Python Version Selection

### Recommended: Python 3.12
- **Best compatibility** with CUDA-enabled PyTorch
- Supported by latest PyTorch releases
- More stable than bleeding-edge versions

### Alternatives:
- **Python 3.10-3.11**: Excellent compatibility
- **Python 3.13**: Works but newer
- **Python 3.14+**: Experimental; may have issues with some CUDA builds

### Avoid:
- **Python 3.8-3.9**: Too old; missing modern features
- **Python 3.14+**: Too new; bleeding edge

**Check available Python versions:**
```powershell
py --list
```

---

## Step-by-Step Setup

### Step 1: Remove Existing Virtual Environment (if needed)
```powershell
# Navigate to project directory
cd "path\to\your\project"

# Remove old venv
if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
```

### Step 2: Create Python 3.12 Virtual Environment
```powershell
# Create new venv with Python 3.12
py -3.12 -m venv .venv

# Activate the environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip
```

### Step 3: Install CUDA-Enabled PyTorch

**For GTX 860M (and other Maxwell/Pascal GPUs):**

```powershell
# Uninstall any existing PyTorch (if present)
pip uninstall -y torch torchvision torchaudio

# Install PyTorch with CUDA 12.4 support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**Alternative CUDA versions:**
- CUDA 11.8: `--index-url https://download.pytorch.org/whl/cu118`
- CUDA 12.1: `--index-url https://download.pytorch.org/whl/cu121`
- CPU only: `--index-url https://download.pytorch.org/whl/cpu`

**File sizes (be patient):**
- PyTorch: ~2.5 GB
- TorchVision: ~6 MB
- TorchAudio: ~4 MB

### Step 4: Verify GPU Detection

```powershell
python -c "import torch; print(torch.__version__); print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

**Expected output:**
```
2.6.0+cu124
CUDA: True
NVIDIA GeForce GTX 860M
```

### Step 5: Install Project Dependencies

```powershell
# Install from requirements.txt
pip install -r requirements.txt

# Or install packages individually
pip install kreuzberg pytesseract transformers flet tqdm psutil python-dotenv
```

---

## Verification Tests

### Full System Test
Create a test script `gpu_test.py`:

```python
import torch
import sys

print("=" * 70)
print("🔍 GPU Detection Test")
print("=" * 70)

# Python version
print(f"\n✓ Python: {sys.version.split()[0]}")

# PyTorch version
print(f"✓ PyTorch: {torch.__version__}")

# CUDA availability
cuda_available = torch.cuda.is_available()
print(f"✓ CUDA Available: {cuda_available}")

if cuda_available:
    # GPU details
    print(f"\n🎮 GPU Information:")
    print(f"  - Device: {torch.cuda.get_device_name(0)}")
    print(f"  - Memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
    print(f"  - Compute Capability: {torch.cuda.get_device_capability(0)}")
    print(f"  - CUDA Version: {torch.version.cuda}")
    
    # Simple CUDA operation test
    print(f"\n🧪 Testing CUDA operations...")
    x = torch.rand(1000, 1000).cuda()
    y = torch.rand(1000, 1000).cuda()
    z = torch.matmul(x, y)
    print(f"  ✅ Matrix multiplication on GPU successful!")
else:
    print(f"\n❌ CUDA not available - running in CPU mode")

print("\n" + "=" * 70)
```

Run with:
```powershell
python gpu_test.py
```

### Expected Results
```
======================================================================
🔍 GPU Detection Test
======================================================================

✓ Python: 3.12.10
✓ PyTorch: 2.6.0+cu124
✓ CUDA Available: True

🎮 GPU Information:
  - Device: NVIDIA GeForce GTX 860M
  - Memory: 2.0 GB
  - Compute Capability: (5, 0)
  - CUDA Version: 12.4

🧪 Testing CUDA operations...
  ✅ Matrix multiplication on GPU successful!

======================================================================
```

---

## Troubleshooting

### Problem: `CUDA: False` (GPU not detected)

**Solutions:**

1. **Verify driver is working:**
   ```powershell
   nvidia-smi
   ```
   If this fails, reinstall NVIDIA driver.

2. **Check PyTorch version:**
   ```powershell
   pip show torch
   ```
   - If version ends with `+cpu`, you installed CPU-only version
   - Reinstall with CUDA: `pip install torch --index-url https://download.pytorch.org/whl/cu124`

3. **Verify Python version:**
   ```powershell
   python --version
   ```
   - Use Python 3.10-3.13 for best compatibility
   - Recreate venv if using incompatible version

4. **Check CUDA availability in detail:**
   ```powershell
   python -c "import torch; print('CUDA compiled:', torch.backends.cuda.is_built()); print('CUDA runtime:', torch.cuda.is_available()); print('Device count:', torch.cuda.device_count())"
   ```

### Problem: Installation fails or times out

1. **Increase timeout and retry:**
   ```powershell
   pip install --timeout=300 torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
   ```

2. **Use proxy (if behind firewall):**
   ```powershell
   pip install --proxy=http://proxy:port torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
   ```

3. **Download wheels manually:**
   - Visit: https://download.pytorch.org/whl/cu124/
   - Download torch, torchvision, torchaudio wheels for your Python version
   - Install: `pip install torch-*.whl torchvision-*.whl torchaudio-*.whl`

### Problem: Out of memory errors during training

**GTX 860M has only 2GB VRAM** - use these strategies:

1. **Reduce batch size:**
   ```python
   batch_size = 1  # or 2 max
   ```

2. **Use gradient accumulation:**
   ```python
   accumulation_steps = 4
   ```

3. **Enable mixed precision (FP16):**
   ```python
   from torch.cuda.amp import autocast, GradScaler
   scaler = GradScaler()
   ```

4. **Clear cache between batches:**
   ```python
   torch.cuda.empty_cache()
   ```

5. **Use CPU for some operations:**
   ```python
   model.to('cpu')  # Move model to CPU for inference
   ```

---

## PyTorch CUDA Version Compatibility

| PyTorch Version | CUDA Version | Python Support | GTX 860M Compatible |
|----------------|--------------|----------------|---------------------|
| 2.6.0+         | 12.4, 12.1, 11.8 | 3.10-3.13 | ✅ Yes |
| 2.4.0-2.5.0    | 12.1, 11.8   | 3.10-3.12 | ✅ Yes |
| 2.2.0-2.3.0    | 12.1, 11.8   | 3.8-3.12  | ✅ Yes |
| 2.0.0-2.1.0    | 11.8, 11.7   | 3.8-3.11  | ✅ Yes |
| 1.13.0-1.13.1  | 11.7, 11.6   | 3.7-3.10  | ✅ Yes |

**CUDA Version in URL:**
- `cu124` = CUDA 12.4
- `cu121` = CUDA 12.1
- `cu118` = CUDA 11.8
- `cpu` = CPU only (no GPU)

---

## Best Practices

### 1. Always use virtual environments
- Isolates dependencies per project
- Prevents version conflicts
- Easy to recreate if corrupted

### 2. Pin PyTorch version in requirements.txt
```txt
# requirements.txt
torch==2.6.0
torchvision==0.21.0
torchaudio==2.6.0
```

### 3. Document CUDA installation method
```txt
# Install with:
# pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

### 4. Test GPU before heavy work
Always run a quick GPU test before starting long-running tasks.

### 5. Monitor GPU usage
```powershell
# In separate terminal, monitor GPU
nvidia-smi -l 1  # Update every 1 second
```

---

## Applying to Other Projects

### Quick Setup Template

```powershell
# 1. Navigate to project
cd "path\to\project"

# 2. Create Python 3.12 venv
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Upgrade pip
python -m pip install --upgrade pip

# 4. Install PyTorch with CUDA
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 5. Verify GPU
python -c "import torch; print(torch.cuda.is_available())"

# 6. Install project dependencies
pip install -r requirements.txt

# 7. Test
python test_setup.py  # or your test script
```

### requirements.txt Template

```txt
# Deep Learning (install separately with CUDA)
# pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
torch>=2.6.0
torchvision>=0.21.0
torchaudio>=2.6.0

# Your project dependencies
numpy>=1.24.0
pillow>=10.0.0
# ... other packages ...
```

---

## Key Takeaways

✅ **GTX 860M works with modern PyTorch** (2.6.0+) and CUDA 12.4  
✅ **No need to install CUDA toolkit** - PyTorch bundles it  
✅ **Modern drivers work fine** - no need to downgrade  
✅ **Python 3.12 is the sweet spot** for compatibility  
✅ **2GB VRAM is tight** - use small batch sizes and optimizations  

---

## References

- PyTorch Installation: https://pytorch.org/get-started/locally/
- NVIDIA Driver Archive: https://www.nvidia.com/Download/index.aspx
- CUDA Compute Capability: https://developer.nvidia.com/cuda-gpus
- PyTorch CUDA Wheels: https://download.pytorch.org/whl/

---

## Tested Configuration

- **Date**: February 7, 2026
- **GPU**: NVIDIA GeForce GTX 860M
- **Driver**: 581.80
- **Python**: 3.12.10
- **PyTorch**: 2.6.0+cu124
- **OS**: Windows 11
- **Result**: ✅ GPU detected and working

---

*Last updated: February 7, 2026*
