# GPU Setup (EasyOCR / TrOCR / PaddleOCR GPU)

EasyOCR e TrOCR usam o **wheel CUDA do PyTorch**.  
PaddleOCR GPU usa **RapidOCR + onnxruntime-gpu** (PP-OCR ONNX na CUDA). Não instale os pacotes Python `paddleocr` / `paddlepaddle`.  
O Kreuzberg 4.10 empacota ORT só-CPU — o backend nativo Paddle não acelera em GPU nesta versão.  
Conflitos: [`docs/DEPENDENCY_CONFLICTS.md`](docs/DEPENDENCY_CONFLICTS.md).

## Current target (RTX 50 / Blackwell)

| Item | Value |
|------|--------|
| GPU | RTX 50-series (e.g. RTX 5060 Ti), Blackwell `sm_120` |
| Driver | 580+ (`nvidia-smi` reports CUDA 13.0 capability) |
| Python | **3.12** |
| PyTorch | **2.13.0+cu130** (or newer `+cu130`) |
| Index | `https://download.pytorch.org/whl/cu130` |

Older tags (`cu124`, `cu126`, …) **do not** include `sm_120`. Use **cu130** (or newer Blackwell-capable builds) on this hardware.

You do **not** need a system CUDA development toolkit for this app — the PyTorch wheel ships the runtime. Only the NVIDIA driver is required.

## Quick setup

```bash
# From project root, with uv + Python 3.12 venv
./scripts/update_deps.sh --sync --cuda cu130

# Or sync pinned requirements:
uv pip sync requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130 \
  --index-strategy unsafe-best-match
```

## Verify

```bash
nvidia-smi
.venv/bin/python -c "import torch; print(torch.__version__); print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

Expect something like `2.13.0+cu130`, `CUDA: True`, and your RTX 50 GPU name.

## Common issues

- **`+cpu` torch** — reinstall via the cu130 index / `update_deps.sh` (never mix PyPI CPU torch with CUDA).
- **Python ≥ 3.13** — CUDA wheels often lag; recreate the venv on 3.12.
- **PaddlePaddle Python packages** — uninstall `paddleocr` / `paddlepaddle*`; they conflict with PyTorch. PaddleOCR GPU is RapidOCR + onnxruntime-gpu.
- **`opencv-python` (GUI)** — RapidOCR lists it as dependency; keep **only** `opencv-python-headless`. `uv pip install rapidocr --no-deps` if pip tries to pull the GUI package.
- **Sandbox / restricted env** — `torch.cuda` may fail inside Cursor sandbox while `nvidia-smi` works on the host; test outside the sandbox.

## PaddleOCR GPU (RapidOCR + onnxruntime-gpu)

O Kreuzberg 4.10.2 **empacota um ONNX Runtime só-CPU** (`ort-bundled`) e **ignora** `ORT_DYLIB_PATH`. Pedir `AccelerationConfig(provider="cuda")` no backend nativo sempre falha nesta versão.

O modo **PaddleOCR GPU - 300 DPI** usa **RapidOCR** (modelos PP-OCR ONNX, reconhecedor latin PP-OCRv5) com **`onnxruntime-gpu` CUDA 13**, as mesmas libs CUDA do PyTorch `cu130`. Não instale `paddlepaddle-gpu`.

```bash
uv pip install "onnxruntime-gpu>=1.27" rapidocr
# Use opencv-python-headless (já no projeto). Não instale opencv-python.
```

Verificação:

```bash
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# Deve incluir CUDAExecutionProvider
```

O bootstrap (`utils/ort_runtime.py`) coloca `torch/lib` no `PATH` e faz preload das DLLs CUDA antes das sessões ORT.

Não misture isso com `paddlepaddle-gpu`.

## VLM fallback (formulários / tabelas)

Quando os templates determinísticos não preenchem campos suficientes, o engine pode
chamar um VLM local via endpoint **OpenAI-compatível** (`VLM_BASE_URL` / `VLM_MODEL`
em `config.py`). Na UI, o dropdown **VLM fallback** escolhe:

- **Desligado (só templates)** — sem VLM
- **Qwen2.5-VL (Ollama)** — `http://127.0.0.1:11434/v1`
- **Nemotron Parse (vLLM)** — `http://127.0.0.1:8000/v1`

Arquivos gerados incluem modo + modelo (`gpu_nemotron`, `gpu_qwen`, `gpu_nenhum`):

- Markdown: `{stem}_ocr_{modo}_{modelo}.md`
- Log da UI: `{stem}_log_conversao_{modo}_{modelo}_{timestamp}.txt`
- Auditoria: `logs/{CNJ}_{modo}_{modelo}_{timestamp}.log`
- Sessão: `logs/{CNJ}_ocr_{modo}_{modelo}_{timestamp}.log` (renomeado ao clicar PROCESSAR PDF)

### Qwen2.5-VL via Ollama (padrão)

```bash
ollama pull qwen2.5vl:7b
# API OpenAI-compatível em http://127.0.0.1:11434/v1
```

`VLM_MODEL=qwen2.5vl:7b` cabe na RTX 5060 Ti 16GB. Aceita prompt livre (descreve
logos/assinaturas/QR codes).

### NVIDIA Nemotron Parse 2.0 via vLLM (candidato a padrão)

Use um **venv separado** (não o `.venv` do OCR). O vLLM/Triton precisa dos headers
do Python (`Python.h`). O sampler FlashInfer do vLLM também precisa de **nvcc**
(CUDA Toolkit 13.0, alinhado ao driver 580). Não use o `nvidia-cuda-toolkit` 12.0
do Ubuntu/Pop — é velho demais para RTX 50.

```bash
sudo apt install python3.12-dev
./scripts/install_cuda_toolkit.sh  # só toolkit 13.0; NÃO instala/troca o driver
./scripts/setup_nemotron_venv.sh   # cria .venv-nemotron + baixa o modelo
./scripts/start_nemotron_parse.sh  # sobe em http://127.0.0.1:8000/v1
```

Se `nvcc` existir, o start script liga FlashInfer e coloca `.venv-nemotron/bin`
no `PATH` (o JIT precisa do `ninja` do venv). Sem toolkit, cai no sampler Triton.

O script usa `--gpu-memory-utilization 0.70` (o padrão do vLLM é 0.92 e falha na RTX 16GB se o desktop já ocupar ~1 GiB). Ajuste se precisar:

```bash
NEMOTRON_GPU_MEM=0.60 ./scripts/start_nemotron_parse.sh   # mais folga p/ EasyOCR
NEMOTRON_GPU_MEM=0.85 ./scripts/start_nemotron_parse.sh   # só Nemotron, sem OCR GPU
```

Na UI, escolha **Nemotron Parse (vLLM)**. Modelo ~1B, especializado em document parsing.

Não descreve logos/assinaturas (tarefa fixa). Validar português no PDF-gabarito
(`scripts/compare_extraction.py`) antes de promover a padrão.

### NVIDIA NIM API (opcional, envia documentos à nuvem)

```bash
export VLM_BASE_URL=https://integrate.api.nvidia.com/v1
export NVIDIA_API_KEY=nvapi-...
export VLM_MODEL=nvidia/nemotron-parse
```

Desligado por padrão. Use só para testes rápidos — processos judiciais não devem
sair da máquina.

## Older GPUs

Historical Maxwell notes (GTX 860M / cu124): [`CUDA_SETUP_GTX860M.md`](CUDA_SETUP_GTX860M.md). Prefer this file + `DEPENDENCY_CONFLICTS.md` for current machines.
