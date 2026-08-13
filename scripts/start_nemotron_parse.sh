#!/usr/bin/env bash
# Serve NVIDIA Nemotron Parse 2.0 via vLLM (OpenAI-compatible on :8000).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv-nemotron"
MODEL_ID="${NEMOTRON_MODEL_ID:-nvidia/NVIDIA-Nemotron-Parse-2.0}"
PORT="${NEMOTRON_PORT:-8000}"
# Default 0.70: RTX 16GB often has ~1 GiB already used (desktop). 0.92 is vLLM default
# and fails when free VRAM < 92%. Leave headroom for KreuzbergParser EasyOCR.
GPU_MEM="${NEMOTRON_GPU_MEM:-0.70}"
MAX_NUM_SEQS="${NEMOTRON_MAX_NUM_SEQS:-4}"

if [[ ! -x "${VENV}/bin/vllm" ]]; then
  echo "venv Nemotron não encontrado. Rode: ${ROOT}/scripts/setup_nemotron_venv.sh" >&2
  exit 1
fi

# FlashInfer JIT chama `ninja` no PATH. O pacote pip fica em .venv-nemotron/bin.
export PATH="${VENV}/bin:${PATH}"

# Triton (vLLM sampler) JIT-compiles cuda_utils.c and needs Python.h.
PY_VER="$("${VENV}/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ ! -f "/usr/include/python${PY_VER}/Python.h" ]]; then
  echo "Falta Python.h (headers de desenvolvimento do Python ${PY_VER})." >&2
  echo "O Triton não consegue compilar kernels CUDA sem isso." >&2
  echo "Instale e rode o script de novo:" >&2
  echo "  sudo apt install python${PY_VER}-dev" >&2
  exit 1
fi

# FlashInfer JIT precisa de nvcc. Com CUDA Toolkit 13.0, use FlashInfer;
# sem nvcc, caia no sampler Triton/PyTorch.
if [[ -z "${CUDA_HOME:-}" ]]; then
  for cuda_dir in /usr/local/cuda /usr/local/cuda-13.0; do
    if [[ -x "${cuda_dir}/bin/nvcc" ]]; then
      export CUDA_HOME="${cuda_dir}"
      break
    fi
  done
fi
if [[ -n "${CUDA_HOME:-}" ]]; then
  export PATH="${CUDA_HOME}/bin:${PATH}"
  export LD_LIBRARY_PATH="${CUDA_HOME}/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
if [[ -z "${VLLM_USE_FLASHINFER_SAMPLER:-}" ]]; then
  if command -v nvcc >/dev/null 2>&1; then
    export VLLM_USE_FLASHINFER_SAMPLER=1
  else
    export VLLM_USE_FLASHINFER_SAMPLER=0
    echo "Aviso: nvcc não encontrado; sampler Triton. Instale com: ${ROOT}/scripts/install_cuda_toolkit.sh" >&2
  fi
fi

IFS=$'\t' read -r MODEL_DIR CHAT_TEMPLATE PATCH_DIR < <(
  "${VENV}/bin/python" - <<PY
from pathlib import Path
from huggingface_hub import snapshot_download
p = Path(snapshot_download("${MODEL_ID}"))
chat = p / "chat_template.jinja"
patch = p / "vllm_tied_patch"
print(
    f"{p}\t{chat if chat.is_file() else ''}\t{patch if patch.is_dir() else ''}"
)
PY
)

if [[ -n "${PATCH_DIR}" ]]; then
  export PYTHONPATH="${PATCH_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
  echo "PYTHONPATH += ${PATCH_DIR}"
fi

ARGS=(
  serve "${MODEL_ID}"
  --dtype bfloat16
  --max-num-seqs "${MAX_NUM_SEQS}"
  --gpu-memory-utilization "${GPU_MEM}"
  --limit-mm-per-prompt '{"image": 1}'
  --trust-remote-code
  --port "${PORT}"
)
if [[ -n "${CHAT_TEMPLATE}" ]]; then
  ARGS+=(--chat-template "${CHAT_TEMPLATE}")
fi

echo "Servindo ${MODEL_ID} em http://127.0.0.1:${PORT}/v1 (gpu-mem=${GPU_MEM}, max-seqs=${MAX_NUM_SEQS})"
exec "${VENV}/bin/vllm" "${ARGS[@]}"
