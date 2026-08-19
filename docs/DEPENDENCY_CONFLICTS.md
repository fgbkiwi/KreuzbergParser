# Conflitos de dependências (KreuzbergParser)

Guia curto para manter o stack Python–PyTorch–OCR **sem misturas incompatíveis**.  
Orientação atual de GPU: [`GPU_SETUP.md`](../GPU_SETUP.md).

## Stack atual (referência)

| Item | Valor |
|------|--------|
| Python | **3.12** (wheels CUDA; evitar ≥3.13 neste projeto) |
| PyTorch | **2.13.0+cu130** |
| GPU alvo | **NVIDIA GeForce RTX 5060 Ti 16 GB** (Blackwell `sm_120`) |
| OpenCV | um único `cv2`: `opencv-contrib-python==4.10.0.84` (PaddleOCR GPU) |

## Regras — não misturar

### 1. Nunca instalar `torch` do PyPI (CPU) junto com o índice CUDA

O wheel padrão no PyPI é **CPU** (`+cpu` ou sem `+cuXXX`). Se ele entrar no ambiente ao mesmo tempo que builds CUDA (`+cu130`), EasyOCR/TrOCR podem cair em CPU ou quebrar imports.

- Resolver/instalar `torch` / `torchvision` **só** a partir do índice PyTorch CUDA.
- Use o script `./scripts/update_deps.sh` (merge com constraints) em vez de `pip install torch` solto.

### 2. Não misturar PaddlePaddle (framework) com o stack PyTorch

`paddlepaddle` / `paddlepaddle-gpu` e `rapidocr` competem por CUDA e **continuam proibidos**.

O modo **PaddleOCR GPU** configura o Kreuzberg nativo (`AccelerationConfig(provider="cuda")` + `ORT_DYLIB_PATH` + `onnxruntime-gpu`). O wheel 4.10.2 empacota ORT só-CPU e ignora `ORT_DYLIB_PATH`; nesse caso o app usa `paddleocr` + `onnxruntime-gpu` (sem `paddlepaddle-gpu`). Se o CUDA não carregar, o modo **falha** — não cai para CPU.

```bash
# Framework Paddle / RapidOCR (proibidos):
uv pip uninstall paddlepaddle paddlepaddle-gpu rapidocr
```

### 3. Um único pacote OpenCV (`cv2`)

Vários wheels (`opencv-python`, `opencv-python-headless`, `opencv-contrib-python`) fornecem o módulo `cv2`. Ter dois no mesmo venv causa imports imprevisíveis.

- Com PaddleOCR GPU: manter **`opencv-contrib-python==4.10.0.84`**
- Evitar: `opencv-python` e `opencv-python-headless` em paralelo com o contrib

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
