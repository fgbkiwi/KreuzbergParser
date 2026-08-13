# Conflitos de dependências (KreuzbergParser)

Guia curto para manter o stack Python–PyTorch–OCR **sem misturas incompatíveis**.  
Orientação atual de GPU: [`GPU_SETUP.md`](../GPU_SETUP.md).

## Stack atual (referência)

| Item | Valor |
|------|--------|
| Python | **3.12** (wheels CUDA; evitar ≥3.13 neste projeto) |
| PyTorch | **2.13.0+cu130** |
| GPU alvo | **RTX 50-series / Blackwell** (`sm_120`) |
| OpenCV | `opencv-python-headless` (não o pacote com GUI) |

## Regras — não misturar

### 1. Nunca instalar `torch` do PyPI (CPU) junto com o índice CUDA

O wheel padrão no PyPI é **CPU** (`+cpu` ou sem `+cuXXX`). Se ele entrar no ambiente ao mesmo tempo que builds CUDA (`+cu130`), EasyOCR/TrOCR podem cair em CPU ou quebrar imports.

- Resolver/instalar `torch` / `torchvision` **só** a partir do índice PyTorch CUDA.
- Use o script `./scripts/update_deps.sh` (merge com constraints) em vez de `pip install torch` solto.

### 2. Não misturar PaddleOCR / PaddlePaddle com o stack PyTorch

O modo GPU deste app usa **EasyOCR + TrOCR (PyTorch)**. Pacotes `paddleocr`, `paddlepaddle`, `paddlepaddle-gpu`, `paddlex` competem por CUDA/OpenCV e já foram removidos do fluxo GPU.

```bash
# Se ainda estiverem instalados:
uv pip uninstall paddleocr paddlepaddle paddlepaddle-gpu paddlex
```

### 3. Preferir `opencv-python-headless`; não instalar `opencv-python` em paralelo

Os dois pacotes fornecem o módulo `cv2`. Ter os dois no mesmo venv causa imports imprevisíveis.

- Manter: `opencv-python-headless`
- Evitar: `opencv-python` (GUI)

### 4. Toolkit CUDA do sistema: só se for compilar kernels (vLLM / FlashInfer)

O wheel do PyTorch **já traz** o runtime CUDA. O KreuzbergParser (EasyOCR/TrOCR)
**não** precisa de `nvcc`. Exceção: o servidor Nemotron Parse (vLLM + FlashInfer JIT)
precisa do **CUDA Toolkit 13.0**, sem trocar o driver Pop!_OS:

```bash
./scripts/install_cuda_toolkit.sh
```

Não instale o `nvidia-cuda-toolkit` 12.0 do Ubuntu/Pop neste hardware (RTX 50 / cu130).
Não instale o metapacote `cuda` da NVIDIA (ele puxa driver e conflita com o 580 do Pop).

### 5. Python ≥ 3.13: wheels CUDA atrasam

No índice PyTorch, builds CUDA para CPython novo costumam demorar. O `update_deps.sh` resolve em **3.12** quando o `.venv` é ≥3.13. Preferir criar o venv com Python 3.12.

### 6. Não misturar vLLM / Nemotron Parse no `.venv` do OCR

O servidor Nemotron Parse usa **`.venv-nemotron`** (`scripts/setup_nemotron_venv.sh`).
vLLM puxa outra pinagem de PyTorch/transformers e quebra EasyOCR/TrOCR se entrar no `.venv` principal.

### 7. `TESSDATA_PREFIX` do projeto vs Tesseract do sistema

Kreuzberg embute Tesseract e usa `TESSDATA_PREFIX` apontando para `tessdata/` do projeto (`por` / `eng`). Um Tesseract instalado no sistema **não** substitui esse cache; não misture `TESSDATA_PREFIX` do sistema com o do app se o OCR falhar por idioma.

## Instalação / sync

```bash
uv pip sync requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --index-strategy unsafe-best-match
```

Ou via script (resolve + opcional sync):

```bash
./scripts/update_deps.sh --sync          # atualiza requirements.txt e instala
./scripts/update_deps.sh --check         # resolve em temp, compara, não grava
```

Verificação periódica (aviso apenas, sem upgrade): `scripts/check_updates.py` — chamado de forma não bloqueante em `main.py`.
