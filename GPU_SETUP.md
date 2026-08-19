# GPU Setup (EasyOCR / TrOCR / PaddleOCR GPU)

EasyOCR e TrOCR usam o **wheel CUDA do PyTorch**.  
PaddleOCR **CPU** usa o backend nativo do Kreuzberg (`AccelerationConfig(provider="cpu")`).  
PaddleOCR **GPU** segue a documentação do Kreuzberg: `AccelerationConfig(provider="cuda")` + `onnxruntime-gpu` + `ORT_DYLIB_PATH`. O wheel 4.10.2 ainda empacota ORT só-CPU (`ort-bundled`) e ignora `ORT_DYLIB_PATH`; nesse caso o app usa PaddleOCR oficial + CUDA (não cai para CPU).  
Conflitos: [`docs/DEPENDENCY_CONFLICTS.md`](docs/DEPENDENCY_CONFLICTS.md).

## Current target (RTX 50 / Blackwell)

| Item | Value |
|------|--------|
| GPU | NVIDIA GeForce RTX 5060 Ti (16 GB), Blackwell `sm_120` |
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
- **PaddlePaddle / RapidOCR** — uninstall `paddlepaddle`, `paddlepaddle-gpu` and `rapidocr`. Do **not** mix them with PyTorch. Official `paddleocr` + `paddlex` is required for PaddleOCR GPU.
- **OpenCV** — keep **one** `cv2`: with PaddleOCR GPU that is `opencv-contrib-python==4.10.0.84` (not `opencv-python-headless` in parallel).
- **Sandbox / restricted env** — `torch.cuda` may fail inside Cursor sandbox while `nvidia-smi` works on the host; test outside the sandbox.

## PaddleOCR CPU (Kreuzberg)

O modo **PaddleOCR CPU - 300 DPI** usa o backend nativo `paddleocr` do Kreuzberg com `AccelerationConfig(provider="cpu")`. Sem pacote extra.

## PaddleOCR GPU (Kreuzberg nativo + onnxruntime-gpu)

Conforme [GPU acceleration](https://docs.kreuzberg.dev/getting-started/installation/#gpu-acceleration) e [PaddleOCR](https://docs.kreuzberg.dev/guides/ocr/#using-paddleocr):

1. PaddleOCR nativo está no Kreuzberg desde 4.8.5 — modelos baixam na primeira uso.
2. Instalar GPU ORT: `uv pip install onnxruntime-gpu>=1.27`
3. `ORT_DYLIB_PATH` aponta para a biblioteca GPU **antes** de `import kreuzberg` (Windows: `...\onnxruntime\capi\onnxruntime.dll`).
4. `ExtractionConfig(acceleration=AccelerationConfig(provider="cuda", device_id=0))` — `device_id` é o índice CUDA da RTX 5060 Ti (0 nesta máquina; override via `CUDA_DEVICE_ID`).
5. O mesmo perfil GPU está em `kreuzberg.toml` (`[acceleration]` + `[ocr.paddle_ocr_config]`). A UI aplica isso em código por modo; a CLI pode usar `--config kreuzberg.toml`.

Este app faz isso em `utils/ort_runtime.py` (`prepare_paddle_gpu_runtime` no `main.py`) e em `_build_extraction_config`.

`LayoutDetectionConfig` e `EmbeddingConfig` aceitam `acceleration`, mas **não são usados** neste pipeline (sem layout detection nem embeddings).

```bash
uv pip install "onnxruntime-gpu>=1.27"
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# Deve incluir CUDAExecutionProvider
```

O Kreuzberg **4.10.2** é compilado com `ort-bundled`: log `ONNX Runtime is bundled; skipping system library discovery`. Pedir CUDA então falha com *CUDA execution provider requested but not available*. O app tenta o nativo primeiro; se recusar CUDA, usa PaddleOCR oficial + `onnxruntime-gpu` (GPU de verdade, não CPU). Não instale `paddlepaddle-gpu` nem `rapidocr`.

Na RTX 5060 Ti (16 GB) o modo GPU usa `model_tier=server`, `padding=16`, lote de 8 páginas e `rec_batch_num=16`.

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
