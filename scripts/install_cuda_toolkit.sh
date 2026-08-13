#!/usr/bin/env bash
# Install CUDA Toolkit 13.0 (nvcc) WITHOUT replacing the Pop!_OS NVIDIA driver.
# Needed by vLLM FlashInfer JIT. Driver 580 already reports CUDA 13.0.
#
# Do NOT use Ubuntu's nvidia-cuda-toolkit (12.0) — it is too old for RTX 50 / cu130.
set -euo pipefail

CUDA_VER="${CUDA_VER:-13.0.2}"
RUNFILE_NAME="${RUNFILE_NAME:-cuda_13.0.2_580.95.05_linux.run}"
URL="${CUDA_RUNFILE_URL:-https://developer.download.nvidia.com/compute/cuda/13.0.2/local_installers/${RUNFILE_NAME}}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/kreuzberg-cuda"
RUNFILE="${CACHE}/${RUNFILE_NAME}"
EXPECTED_BYTES="${CUDA_RUNFILE_BYTES:-4328066903}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Rode sem sudo. O script pede sudo só na instalação." >&2
  exit 1
fi

nvcc_bin=""
for candidate in \
  "${CUDA_HOME:-}/bin/nvcc" \
  /usr/local/cuda/bin/nvcc \
  /usr/local/cuda-13.0/bin/nvcc \
  "$(command -v nvcc 2>/dev/null || true)"; do
  if [[ -n "${candidate}" && -x "${candidate}" ]]; then
    nvcc_bin="${candidate}"
    break
  fi
done

if [[ -n "${nvcc_bin}" ]]; then
  echo "nvcc já instalado: ${nvcc_bin}"
  "${nvcc_bin}" --version
  exit 0
fi

mkdir -p "${CACHE}"
if [[ ! -f "${RUNFILE}" || "$(stat -c%s "${RUNFILE}" 2>/dev/null || echo 0)" -ne "${EXPECTED_BYTES}" ]]; then
  echo "==> baixando ${RUNFILE_NAME} (~4.0 GiB)..."
  curl -L --fail --retry 5 --continue-at - -o "${RUNFILE}.part" "${URL}"
  mv "${RUNFILE}.part" "${RUNFILE}"
fi

chmod +x "${RUNFILE}"
echo "==> instalando só o toolkit em /usr/local/cuda-13.0 (sem driver)"
sudo sh "${RUNFILE}" --silent --toolkit --no-man-page --override

if [[ ! -e /usr/local/cuda && -d /usr/local/cuda-13.0 ]]; then
  sudo ln -s /usr/local/cuda-13.0 /usr/local/cuda
fi

echo
echo "OK. Driver Pop!_OS intacto. nvcc:"
/usr/local/cuda/bin/nvcc --version || /usr/local/cuda-13.0/bin/nvcc --version
echo
echo "Pode apagar o instalador depois (~4 GiB):"
echo "  rm -f ${RUNFILE}"
echo "Suba o Nemotron com:"
echo "  $(cd "$(dirname "$0")/.." && pwd)/scripts/start_nemotron_parse.sh"
