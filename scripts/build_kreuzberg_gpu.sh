#!/usr/bin/env bash
# Compila o wheel Python do Kreuzberg com suporte a GPU (PaddleOCR nativo em CUDA).
#
# Por que isso existe:
#   O wheel do PyPI (kreuzberg==4.10.2) é compilado com a feature Rust
#   `ort-bundled`: o binário linka um ONNX Runtime só-CPU embutido, ignora
#   ORT_DYLIB_PATH e o crate `ort` descarta o registro do CUDAExecutionProvider
#   ("corresponding Cargo feature is not enabled"). Resultado: PaddleOCR nativo
#   nunca roda em GPU, mesmo com onnxruntime-gpu instalado.
#
#   Este script gera o mesmo wheel com a feature `ort-dynamic` (ort/load-dynamic):
#   o binário passa a carregar em runtime a biblioteca apontada por
#   ORT_DYLIB_PATH (a do onnxruntime-gpu instalado via uv), habilitando
#   AccelerationConfig(provider="cuda") no PaddleOCR nativo.
#   Docs: https://docs.kreuzberg.dev/reference/environment-variables/#ort_dylib_path
#
# Pré-requisitos (fora do venv):
#   - Rust (rustup): curl -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.95 --profile minimal
#   - cmake:        uv tool install cmake       (compila leptonica/tesseract vendorizados)
#   - g++/cc/make:  toolchain C/C++ do sistema
#
# Uso:
#   ./scripts/build_kreuzberg_gpu.sh            # clona, compila e instala no .venv
#   ./scripts/build_kreuzberg_gpu.sh --no-install
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KREUZBERG_TAG="v4.10.2"
REPO_URL="https://github.com/kreuzberg-dev/kreuzberg-lts"
SRC_DIR="${TMPDIR:-/tmp}/kreuzberg-gpu-src"
WHEEL_DIR="${ROOT}/vendor/wheels"
DO_INSTALL=1

[[ "${1:-}" == "--no-install" ]] && DO_INSTALL=0

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

command -v cargo >/dev/null || { echo "erro: cargo não encontrado (instale rustup)"; exit 1; }
command -v cmake >/dev/null || { echo "erro: cmake não encontrado (uv tool install cmake)"; exit 1; }
command -v uv    >/dev/null || { echo "erro: uv não encontrado"; exit 1; }

echo "==> Clonando ${REPO_URL} @ ${KREUZBERG_TAG}"
rm -rf "$SRC_DIR"
git clone --depth 1 --branch "$KREUZBERG_TAG" "$REPO_URL" "$SRC_DIR"

echo "==> Habilitando ort-dynamic (load-dynamic) no kreuzberg-py"
# 1) Bindings Python: acrescenta a feature ort-dynamic à dependência kreuzberg.
sed -i 's|kreuzberg = { workspace = true, features = \["bundled-pdfium", "full"\] }|kreuzberg = { workspace = true, features = ["bundled-pdfium", "full", "ort-dynamic"] }|' \
  "$SRC_DIR/crates/kreuzberg-py/Cargo.toml"
grep -q 'ort-dynamic' "$SRC_DIR/crates/kreuzberg-py/Cargo.toml" \
  || { echo "erro: patch do kreuzberg-py/Cargo.toml não aplicou"; exit 1; }

# 2) tls-rustls no lugar de tls-native: evita exigir libssl-dev do sistema.
#    (ort-bundled continua declarado por outras features, mas load-dynamic prevalece.)
sed -i 's|ort-bundled = \["ort/download-binaries", "ort/tls-native"\]|ort-bundled = ["ort/download-binaries", "ort/tls-rustls"]|' \
  "$SRC_DIR/crates/kreuzberg/Cargo.toml"
grep -q 'ort/tls-rustls' "$SRC_DIR/crates/kreuzberg/Cargo.toml" \
  || { echo "erro: patch do kreuzberg/Cargo.toml não aplicou"; exit 1; }

echo "==> Compilando wheel (maturin, perfil release) — pode demorar bastante"
( cd "$SRC_DIR/packages/python" && uvx maturin build --release --out "$WHEEL_DIR" )

WHEEL="$(ls -1 "$WHEEL_DIR"/kreuzberg-*.whl | sort | tail -n 1)"
echo "==> Wheel gerado: $WHEEL"

if [[ "$DO_INSTALL" -eq 1 ]]; then
  echo "==> Instalando no .venv"
  uv pip install --python "${ROOT}/.venv/bin/python" --force-reinstall --no-deps "$WHEEL"
  echo "==> Instalado. O modo PaddleOCR GPU usa este wheel + ORT_DYLIB_PATH."
fi
