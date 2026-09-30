# Conflitos de dependências (KreuzbergParser)

Guia curto para manter o stack Python–ONNX Runtime–OCR **sem misturas incompatíveis**.  
Orientação atual de GPU: [`GPU_SETUP.md`](../GPU_SETUP.md).

## Stack atual (referência)

| Item | Valor |
|------|--------|
| Python | **3.12** (wheels CUDA; evitar ≥3.13 neste projeto) |
| GPU runtime | `onnxruntime-gpu[cuda,cudnn]` (CUDA 13 + cuDNN 9 via wheels `nvidia-*`); **sem PyTorch** |
| GPU alvo | **NVIDIA GeForce RTX 5060 Ti 16 GB** (Blackwell `sm_120`) |
| OpenCV | um único `cv2` (`opencv-python-headless` **4.x**, `<5`) |
| Kreuzberg | wheel local `ort-dynamic` em `vendor/wheels/` (`./scripts/build_kreuzberg_gpu.sh`) |

## Regras — não misturar

### 1. Nenhum PyTorch / transformers no venv do app

O app não usa PyTorch desde a remoção do TrOCR (detecção de manuscrito); a GPU é
detectada via NVML (`nvidia-ml-py`). `torch`, `torchvision`, `triton` e `transformers`
somavam ~3.5 GB e seriam empacotados nos instaladores, estourando o limite de 2 GiB
por asset das Releases do GitHub.

- `check_dep_conflicts.py` **falha** se algum deles estiver instalado.
- `update_deps.ps1` os desinstala; no Linux, `update_deps.sh --sync` os remove.
- As libs CUDA/cuDNN vêm dos extras `onnxruntime-gpu[cuda,cudnn]`, não do torch.

### 2. Nenhum pacote Python do ecossistema Paddle no venv

`paddleocr`, `paddlex`, `paddlepaddle` e `paddlepaddle-gpu` são **proibidos**.  
`easyocr` também é **proibido** (substituído pelo modo GPU com RapidOCR).

O modo **GPU** usa **RapidOCR** + `onnxruntime-gpu` (PP-OCR ONNX em CUDA).  
O modo **PaddleOCR GPU** usa exclusivamente o Kreuzberg nativo: wheel local compilado com `ort-dynamic` (`./scripts/build_kreuzberg_gpu.sh`) + `AccelerationConfig(provider="cuda")` + `ORT_DYLIB_PATH` apontando para a lib do `onnxruntime-gpu`. O wheel do PyPI empacota ORT só-CPU e ignora `ORT_DYLIB_PATH` — por isso a instalação real vem de `vendor/wheels/`. Se o CUDA não carregar, o modo **falha** — não há fallback.

```bash
# Pacotes Paddle / EasyOCR (proibidos):
uv pip uninstall paddleocr paddlex paddlepaddle paddlepaddle-gpu easyocr
```

### 3. Um único pacote OpenCV (`cv2`)

Vários wheels (`opencv-python`, `opencv-python-headless`, `opencv-contrib-python`) fornecem o módulo `cv2`. Ter dois no mesmo venv causa imports imprevisíveis.

- Manter apenas **`opencv-python-headless>=4.8,<5`** (dependência do RapidOCR)
- **Não** instalar OpenCV 5.x neste projeto (`cv2.imdecode` quebrou no caminho GPU/RapidOCR)
- Evitar: `opencv-python` (GUI) e variantes `contrib` em paralelo

### 4. Toolkit CUDA do sistema: só se for compilar kernels (vLLM / FlashInfer)

As wheels `nvidia-*` do `onnxruntime-gpu` **já trazem** o runtime CUDA/cuDNN. O
KreuzbergParser (RapidOCR / PaddleOCR GPU) **não** precisa de `nvcc`. Exceção: o servidor Nemotron Parse (vLLM + FlashInfer JIT)
precisa do **CUDA Toolkit 13.0**, sem trocar o driver Pop!_OS:

```bash
./scripts/install_cuda_toolkit.sh
```

Não instale o `nvidia-cuda-toolkit` 12.0 do Ubuntu/Pop neste hardware (RTX 50 / CUDA 13).
Não atualize para o CUDA Toolkit 13.4: o `.venv-nemotron` usa torch `+cu130` e o `nvcc`
precisa continuar na 13.0 (ver `GPU_SETUP.md`).
Não instale o metapacote `cuda` da NVIDIA (ele puxa driver e conflita com o 580 do Pop).

### 5. Python ≥ 3.13: wheels CUDA atrasam

Wheels CUDA (ORT, `nvidia-*`) para CPython novo costumam demorar, e os instaladores embutem CPython 3.12. O `update_deps.sh` resolve em **3.12** quando o `.venv` é ≥3.13. Preferir criar o venv com Python 3.12.

### 6. Não misturar vLLM / Nemotron Parse no `.venv` do OCR

O servidor Nemotron Parse usa **`.venv-nemotron`** (`scripts/setup_nemotron_venv.sh`).
vLLM puxa PyTorch/transformers, que não podem entrar no `.venv` principal (ver regra 1).

### 7. `TESSDATA_PREFIX` do projeto vs Tesseract do sistema

Kreuzberg embute Tesseract e usa `TESSDATA_PREFIX` apontando para `tessdata/` do projeto (`por` / `eng`). Um Tesseract instalado no sistema **não** substitui esse cache; não misture `TESSDATA_PREFIX` do sistema com o do app se o OCR falhar por idioma.

## Instalação / sync

### Linux / macOS

```bash
uv pip sync requirements.txt
```

Ou via script (resolve + opcional sync):

```bash
./scripts/update_deps.sh --sync          # atualiza requirements.txt e instala
./scripts/update_deps.sh --check         # resolve em temp, compara, não grava
```

O `--sync` reinstala automaticamente o wheel GPU local do Kreuzberg
(`vendor/wheels/kreuzberg-*.whl`) por cima do wheel CPU do PyPI. Se o wheel
não existir, gere-o com `./scripts/build_kreuzberg_gpu.sh`.

### Windows

**Não** use `uv pip sync requirements.txt` (pins de wheels `nvidia-*` resolvidos no Linux). Use:

```powershell
.\scripts\update_deps.ps1 -Sync          # requirements.in + checagem
.\scripts\update_deps.ps1 -Check         # só valida conflitos no .venv
```

O script remove pacotes Paddle/EasyOCR / PyTorch / `nvidia-cufile*` se aparecerem e
normaliza OpenCV para um único provedor `cv2` (`opencv-python-headless`).

Verificação periódica (aviso apenas, sem upgrade): `scripts/check_updates.py` — chamado de forma não bloqueante em `main.py`.
