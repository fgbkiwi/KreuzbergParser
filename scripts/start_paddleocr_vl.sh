#!/usr/bin/env bash
# Serve PaddleOCR-VL 1.6 via vLLM (OpenAI-compatible on :8001).
# Reuses the Nemotron venv (vLLM); port 8001 so both servers can coexist.
# Recipe: https://recipes.vllm.ai/PaddlePaddle/PaddleOCR-VL-1.6
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv-nemotron"
MODEL_ID="${PADDLEOCR_VL_MODEL_ID:-PaddlePaddle/PaddleOCR-VL-1.6}"
PORT="${PADDLEOCR_VL_PORT:-8001}"
# 0.9B model: a small share of the 16GB card is enough and leaves room for
# RapidOCR / PaddleOCR GPU in the main app.
GPU_MEM="${PADDLEOCR_VL_GPU_MEM:-0.30}"
# The model's native window is 131072 tokens; its KV cache alone would not fit
# in the share above. One page needs far less.
MAX_LEN="${PADDLEOCR_VL_MAX_LEN:-16384}"

if [[ ! -x "${VENV}/bin/vllm" ]]; then
  echo "venv do vLLM não encontrado. Rode: ${ROOT}/scripts/setup_nemotron_venv.sh" >&2
  exit 1
fi

export PATH="${VENV}/bin:${PATH}"

echo "Servindo ${MODEL_ID} em http://127.0.0.1:${PORT}/v1 (gpu-mem=${GPU_MEM})"
exec "${VENV}/bin/vllm" serve "${MODEL_ID}" \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --gpu-memory-utilization "${GPU_MEM}" \
  --max-model-len "${MAX_LEN}" \
  --port "${PORT}"
