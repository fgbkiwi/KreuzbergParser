#!/usr/bin/env bash
# Create an isolated venv for NVIDIA Nemotron Parse 2.0 + vLLM.
# Do NOT mix this with the main KreuzbergParser .venv (RapidOCR/PyTorch pins).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv-nemotron"
MODEL_ID="${NEMOTRON_MODEL_ID:-nvidia/NVIDIA-Nemotron-Parse-2.0}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv não encontrado. Instale: https://docs.astral.sh/uv/" >&2
  exit 1
fi

# Triton (vLLM) JIT-compiles CUDA helpers and needs Python.h.
PY_VER="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ ! -f "/usr/include/python${PY_VER}/Python.h" ]]; then
  echo "Falta Python.h. Instale e rode este script de novo:" >&2
  echo "  sudo apt install python${PY_VER}-dev" >&2
  exit 1
fi

echo "==> venv: ${VENV} (${PYTHON_BIN})"
uv venv "${VENV}" --python "${PYTHON_BIN}" --seed

echo "==> instalando vLLM + dependências do Nemotron Parse (torch cu130 se possível)"
if UV_TORCH_BACKEND=cu130 uv pip install --python "${VENV}/bin/python" \
    vllm ninja albumentations timm open_clip_torch einops huggingface_hub accelerate; then
  echo "vLLM instalado com UV_TORCH_BACKEND=cu130"
else
  echo "UV_TORCH_BACKEND=cu130 falhou; tentando índice PyTorch cu130 + vLLM PyPI"
  uv pip install --python "${VENV}/bin/python" \
    torch torchvision \
    --extra-index-url https://download.pytorch.org/whl/cu130
  uv pip install --python "${VENV}/bin/python" \
    vllm ninja albumentations timm open_clip_torch einops huggingface_hub accelerate \
    --extra-index-url https://download.pytorch.org/whl/cu130 \
    --index-strategy unsafe-best-match
fi

echo "==> baixando pesos ${MODEL_ID} (cache Hugging Face)"
"${VENV}/bin/python" - <<PY
from huggingface_hub import snapshot_download
path = snapshot_download("${MODEL_ID}")
print(path)
PY

echo
echo "OK. Ative o servidor com:"
echo "  ${ROOT}/scripts/start_nemotron_parse.sh"
echo "Na UI do KreuzbergParser, escolha: Nemotron Parse (vLLM)"
