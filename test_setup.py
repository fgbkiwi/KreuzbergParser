"""
Quick start script to test Kreuzberg installation and basic functionality
"""
import sys
from pathlib import Path

print("=" * 70)
print("🧪 TESTE RÁPIDO - Sistema Inteligente de OCR")
print("=" * 70)
print()

# Test 1: Python version
print("✓ Verificando Python version...")
py_version = sys.version_info
if py_version.major >= 3 and py_version.minor >= 9:
    print(f"  ✅ Python {py_version.major}.{py_version.minor}.{py_version.micro} OK")
else:
    print(f"  ❌ Python {py_version.major}.{py_version.minor} - Requer 3.9+")
    sys.exit(1)

# Test 2: Kreuzberg
print("\n✓ Verificando Kreuzberg...")
try:
    import kreuzberg
    print(f"  ✅ Kreuzberg instalado")
except ImportError:
    print("  ❌ Kreuzberg não encontrado")
    print("     Instalar com: pip install kreuzberg")
    sys.exit(1)

# Test 3: Flet
print("\n✓ Verificando Flet...")
try:
    import flet
    print(f"  ✅ Flet instalado")
except ImportError:
    print("  ❌ Flet não encontrado")
    print("     Instalar com: pip install flet")
    sys.exit(1)

# Test 4: PyTorch (optional)
print("\n✓ Verificando PyTorch (GPU mode)...")
try:
    import torch
    cuda_available = torch.cuda.is_available()
    if cuda_available:
        print(f"  ✅ PyTorch com CUDA detectado")
        print(f"     GPU: {torch.cuda.get_device_name(0)}")
        vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        print(f"     VRAM: {vram:.1f}GB")
    else:
        print(f"  ⚠️  PyTorch instalado, mas CUDA não disponível")
        print("     Modo GPU não funcionará")
except ImportError:
    print("  ℹ️  PyTorch não instalado (opcional para GPU mode)")

# Test 5: Tesseract
print("\n✓ Verificando Tesseract OCR...")
try:
    import pytesseract
    from PIL import Image
    import subprocess
    
    result = subprocess.run(
        ['tesseract', '--version'],
        capture_output=True,
        text=True,
        timeout=5
    )
    if result.returncode == 0:
        version_line = result.stdout.split('\n')[0]
        print(f"  ✅ {version_line}")
    else:
        raise Exception("Tesseract not found")
        
except Exception as e:
    print("  ❌ Tesseract não encontrado ou não configurado")
    print("     Instalar Tesseract OCR:")
    print("     - Windows: https://github.com/UB-Mannheim/tesseract/wiki")
    print("     - Linux: sudo apt install tesseract-ocr tesseract-ocr-por")
    print("     - Mac: brew install tesseract tesseract-lang")

# Test 6: Project structure
print("\n✓ Verificando estrutura do projeto...")
required_files = [
    'main.py',
    'config.py',
    'requirements.txt',
    'core/kreuzberg_engine.py',
    'ui/app.py'
]

all_ok = True
for file in required_files:
    file_path = Path(file)
    if file_path.exists():
        print(f"  ✅ {file}")
    else:
        print(f"  ❌ {file} não encontrado")
        all_ok = False

# Final verdict
print("\n" + "=" * 70)
if all_ok:
    print("🎉 SISTEMA PRONTO PARA USO!")
    print()
    print("Para iniciar a aplicação:")
    print("  python main.py")
else:
    print("⚠️  ALGUNS COMPONENTES ESTÃO FALTANDO")
    print("   Revise os erros acima antes de executar")

print("=" * 70)
