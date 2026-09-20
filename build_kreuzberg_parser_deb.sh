#!/usr/bin/env bash
# ============================================================
# Build script: KreuzbergParser (.deb amd64) para Pop!_OS / Ubuntu
#
# Espelha build_kreuzberg_parser_pynsist.ps1: empacota CPython 3.12 +
# venv CUDA + app em /opt/kreuzberg-parser, com atalho no menu.
#
# Pré-requisitos (build no Linux):
#   uv, dpkg-deb, Python 3.12 (via uv python)
#   ./scripts/update_deps.sh --sync --cuda cu130
#   ./scripts/build_kreuzberg_gpu.sh   # gera vendor/wheels/kreuzberg-*.whl
#   gh autenticado — só se for publicar
#
# Uso:
#   ./build_kreuzberg_parser_deb.sh --no-publish          # gera .deb (sem bump)
#   ./build_kreuzberg_parser_deb.sh --bump patch          # bump + .deb + upload
#   ./build_kreuzberg_parser_deb.sh --bump minor --no-publish
#   ./build_kreuzberg_parser_deb.sh --cuda cu130 --no-publish
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

APP_NAME="KreuzbergParser"
PACKAGE_NAME="kreuzberg-parser"
OPT_PREFIX="/opt/kreuzberg-parser"
GITHUB_REPO="fgbkiwi/KreuzbergParser"
ICON_SOURCE="assets/kreuzberg-parser.png"
PACKAGING_DIR="packaging/linux"
REQ_FILE="requirements.txt"
BUMP_SCRIPT="bump_version.py"

CUDA_TAG="cu130"
DO_PUBLISH=1
BUMP_PART=""  # empty = no bump (default: attach to same tag as Windows)

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "==> $*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --no-publish) DO_PUBLISH=0; shift ;;
    --no-bump) BUMP_PART=""; shift ;;
    --bump)
      [[ $# -ge 2 ]] || die "--bump requires patch|minor|major"
      BUMP_PART="$2"
      shift 2
      ;;
    --cuda)
      [[ $# -ge 2 ]] || die "--cuda requires a tag (e.g. cu130)"
      CUDA_TAG="$2"
      shift 2
      ;;
    *)
      die "unknown argument: $1 (use --help)"
      ;;
  esac
done

case "$BUMP_PART" in
  ""|patch|minor|major) ;;
  *) die "--bump must be patch, minor, or major (got: $BUMP_PART)" ;;
esac

echo "========================================"
echo " Building ${APP_NAME} Linux .deb"
echo "========================================"

command -v uv >/dev/null 2>&1 || die "uv not found. Install: https://docs.astral.sh/uv/"
command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not found. Install: sudo apt install dpkg-dev"
[[ -f "$REQ_FILE" ]] || die "missing $REQ_FILE — run ./scripts/update_deps.sh first"
[[ -f "$ICON_SOURCE" ]] || die "missing icon: $ICON_SOURCE"
[[ -f "${PACKAGING_DIR}/kreuzberg-parser.desktop" ]] || die "missing packaging templates in ${PACKAGING_DIR}/"
[[ -f "${PACKAGING_DIR}/kreuzberg-parser.wrapper" ]] || die "missing wrapper template"
[[ -f "${PACKAGING_DIR}/splash.py" ]] || die "missing splash.py in ${PACKAGING_DIR}/"

GPU_WHEEL="$(ls -1 "${ROOT}"/vendor/wheels/kreuzberg-*.whl 2>/dev/null | sort | tail -n 1 || true)"
[[ -n "$GPU_WHEEL" ]] || die "vendor/wheels/kreuzberg-*.whl not found. Run: ./scripts/build_kreuzberg_gpu.sh"

pytorch_index_url() {
  case "$1" in
    cpu) echo "https://download.pytorch.org/whl/cpu" ;;
    cu118|cu121|cu124|cu126|cu128|cu130) echo "https://download.pytorch.org/whl/$1" ;;
    *) die "unsupported CUDA tag '$1'" ;;
  esac
}
INDEX_URL="$(pytorch_index_url "$CUDA_TAG")"

if [[ "$DO_PUBLISH" -eq 1 ]]; then
  command -v gh >/dev/null 2>&1 || die "GitHub CLI (gh) not found. Install it or use --no-publish."
  gh auth status >/dev/null 2>&1 || die "GitHub CLI not authenticated. Run 'gh auth login' or use --no-publish."
fi

# --- Version (source of truth: APP_VERSION in main.py) -------------------
if [[ -n "$BUMP_PART" ]]; then
  log "[0/5] Incrementando versão ($BUMP_PART)..."
  [[ -f "$BUMP_SCRIPT" ]] || die "missing $BUMP_SCRIPT"
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    VERSION="$("${ROOT}/.venv/bin/python" "$BUMP_SCRIPT" "$BUMP_PART")"
  else
    VERSION="$(python3 "$BUMP_SCRIPT" "$BUMP_PART")"
  fi
  [[ -n "$VERSION" ]] || die "bump_version.py did not print the new version"
else
  log "[0/5] Mantendo versão atual (sem bump)..."
  VERSION="$(
    sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*["'\'']([0-9]+\.[0-9]+\.[0-9]+)["'\''].*/\1/p' main.py \
      | head -n 1
  )"
  [[ -n "$VERSION" ]] || die "could not read APP_VERSION from main.py"
fi
log "Versão: $VERSION"

TAG="v${VERSION}"
OUT_DEB="${ROOT}/build/deb/${APP_NAME}_${VERSION}_amd64.deb"
STAGING="${ROOT}/build/deb/staging"
OPT_STAGING="${STAGING}${OPT_PREFIX}"
VENV_STAGING="${OPT_STAGING}/venv"
APP_STAGING="${OPT_STAGING}/app"

# --- Clean staging -------------------------------------------------------
log "[1/5] Preparando staging em build/deb/..."
rm -rf "${ROOT}/build/deb"
mkdir -p \
  "$APP_STAGING" \
  "$VENV_STAGING" \
  "${STAGING}/usr/bin" \
  "${STAGING}/usr/share/applications" \
  "${STAGING}/usr/share/icons/hicolor/256x256/apps" \
  "${STAGING}/DEBIAN"

# --- Create venv + install deps ------------------------------------------
log "[2/5] Criando venv CPython 3.12 e instalando dependências (CUDA ${CUDA_TAG})..."
log "  (o stack CUDA torna este passo lento e o .deb grande)"
uv python install 3.12 >/dev/null
uv venv "$VENV_STAGING" --python 3.12 --clear
VENV_PY="${VENV_STAGING}/bin/python"

uv pip sync "$REQ_FILE" \
  --python "$VENV_PY" \
  --extra-index-url "$INDEX_URL" \
  --index-strategy unsafe-best-match

log "  Reinstalando wheel GPU Kreuzberg: ${GPU_WHEEL##*/}"
uv pip install --python "$VENV_PY" --force-reinstall --no-deps "$GPU_WHEEL"

log "  Verificando conflitos de dependências..."
"$VENV_PY" "${ROOT}/scripts/check_dep_conflicts.py" "$CUDA_TAG" "$VENV_PY" \
  || die "check_dep_conflicts.py failed — fix the staging venv before packaging"

log "  Smoke test (cv2 / torch / kreuzberg)..."
"$VENV_PY" - <<'PY' || die "smoke import failed in staging venv"
import cv2  # noqa: F401
import torch  # noqa: F401
import kreuzberg  # noqa: F401
print("smoke ok: cv2", cv2.__version__, "torch", torch.__version__, "kreuzberg", getattr(kreuzberg, "__version__", "?"))
PY

# Rewrite absolute staging paths → final /opt paths (relocatable install).
rewrite_venv_paths() {
  local from="$1"
  local to="$2"
  local cfg="${VENV_STAGING}/pyvenv.cfg"
  if [[ -f "$cfg" ]]; then
    sed -i "s|${from}|${to}|g" "$cfg"
  fi
  # Only rewrite text scripts under bin/ (never the python binary / shared libs).
  if [[ -d "${VENV_STAGING}/bin" ]]; then
    local f base
    for f in "${VENV_STAGING}/bin"/*; do
      [[ -f "$f" && ! -L "$f" ]] || continue
      base="$(basename "$f")"
      # Shebang scripts or virtualenv activate helpers only.
      if [[ "$(head -c 2 "$f" 2>/dev/null || true)" == "#!" ]] \
        || [[ "$base" == activate || "$base" == activate.* || "$base" == Activate* ]]; then
        if grep -qF "$from" "$f" 2>/dev/null; then
          sed -i "s|${from}|${to}|g" "$f"
        fi
      fi
    done
  fi
}
rewrite_venv_paths "$VENV_STAGING" "${OPT_PREFIX}/venv"

# --- Copy application files ----------------------------------------------
log "[3/5] Copiando app, wrapper, desktop e ícone..."
copy_tree() {
  local src="$1"
  local dest="$2"
  mkdir -p "$dest"
  # Prefer rsync if available (excludes caches); else cp -a with prune.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude '.pytest_cache' \
      "$src/" "$dest/"
  else
    cp -a "$src/." "$dest/"
    find "$dest" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    find "$dest" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  fi
}

for mod in core ui utils scripts; do
  [[ -d "$mod" ]] || die "missing directory: $mod"
  copy_tree "$mod" "${APP_STAGING}/${mod}"
done

for f in main.py config.py _kreuzberg_launcher.py kreuzberg.toml; do
  [[ -f "$f" ]] || die "missing file: $f"
  cp -a "$f" "${APP_STAGING}/"
done

mkdir -p "${APP_STAGING}/assets"
cp -a "$ICON_SOURCE" "${APP_STAGING}/assets/"
# GTK splash helper (run via /usr/bin/python3 + python3-gi, not the venv).
install -m 0644 "${PACKAGING_DIR}/splash.py" "${APP_STAGING}/splash.py"
mkdir -p "${APP_STAGING}/packaging/linux"
install -m 0644 "${PACKAGING_DIR}/splash.py" "${APP_STAGING}/packaging/linux/splash.py"

install -m 0755 "${PACKAGING_DIR}/kreuzberg-parser.wrapper" \
  "${STAGING}/usr/bin/kreuzberg-parser"
install -m 0644 "${PACKAGING_DIR}/kreuzberg-parser.desktop" \
  "${STAGING}/usr/share/applications/kreuzberg-parser.desktop"
install -m 0644 "$ICON_SOURCE" \
  "${STAGING}/usr/share/icons/hicolor/256x256/apps/kreuzberg-parser.png"

# --- DEBIAN metadata -----------------------------------------------------
INSTALLED_SIZE_KB="$(du -sk "$STAGING" | awk '{print $1}')"

cat > "${STAGING}/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: amd64
Installed-Size: ${INSTALLED_SIZE_KB}
Maintainer: KreuzbergParser Maintainers <noreply@github.com>
Depends: tesseract-ocr, tesseract-ocr-por, poppler-utils, libgtk-3-0, libgstreamer1.0-0, libmpv1 | libmpv2, python3-gi, gir1.2-gtk-3.0
Suggests: nvidia-driver-580 | nvidia-driver-550 | nvidia-driver-535
Homepage: https://github.com/${GITHUB_REPO}
Description: OCR inteligente para PDFs judiciais (PJe)
 KreuzbergParser empacota Python 3.12 + stack CUDA (PyTorch cu${CUDA_TAG#cu})
 e a UI Flet. Instala em ${OPT_PREFIX}.
 .
 Express/CPU funcionam sem GPU. Modos GPU exigem driver NVIDIA atualizado
 (o wheel do PyTorch traz o runtime CUDA; toolkit não é necessário).
 .
 Dados graváveis: ~/.local/share/KreuzbergParser
EOF

install -m 0755 "${PACKAGING_DIR}/postinst" "${STAGING}/DEBIAN/postinst"
install -m 0755 "${PACKAGING_DIR}/postrm" "${STAGING}/DEBIAN/postrm"

# --- Build .deb ----------------------------------------------------------
log "[4/5] Gerando pacote .deb com dpkg-deb..."
# Exclude build junk; ensure ownership is root:root in the archive.
dpkg-deb --root-owner-group --build "$STAGING" "$OUT_DEB"

[[ -f "$OUT_DEB" ]] || die "dpkg-deb did not produce $OUT_DEB"
DEB_SIZE="$(du -h "$OUT_DEB" | awk '{print $1}')"

echo ""
echo "========================================"
echo " DEB BUILD SUCCESSFUL!"
echo "========================================"
echo "Versão:  $VERSION"
echo "Pacote:  $OUT_DEB"
echo "Tamanho: $DEB_SIZE"

# --- Publish -------------------------------------------------------------
if [[ "$DO_PUBLISH" -eq 0 ]]; then
  log "Publicação no GitHub omitida (--no-publish)."
  echo "Para anexar depois a uma Release existente:"
  echo "  gh release upload ${TAG} \"${OUT_DEB}\" --repo ${GITHUB_REPO} --clobber"
  echo "Ou criar Release se ainda não existir:"
  echo "  gh release create ${TAG} \"${OUT_DEB}\" --repo ${GITHUB_REPO} --title \"${APP_NAME} ${VERSION}\" --latest"
else
  log "[5/5] Publicando ${TAG} no GitHub..."
  NOTES="$(cat <<EOF
Instalador Linux (.deb amd64) do ${APP_NAME} ${VERSION} para Pop!_OS / Ubuntu.

\`\`\`bash
sudo apt install ./${APP_NAME}_${VERSION}_amd64.deb
\`\`\`

Dependências apt (resolvidas pelo apt): tesseract-ocr, tesseract-ocr-por, poppler-utils, libgtk-3-0, gstreamer, libmpv, python3-gi.

GPU: driver NVIDIA atualizado (\`nvidia-smi\`). Express/CPU funcionam sem GPU.
Dados: \`~/.local/share/KreuzbergParser\`
EOF
)"

  if gh release view "$TAG" --repo "$GITHUB_REPO" >/dev/null 2>&1; then
    log "Release ${TAG} já existe — anexando o .deb (sem recriar)."
    gh release upload "$TAG" "$OUT_DEB" --repo "$GITHUB_REPO" --clobber
  else
    log "Criando Release ${TAG} com o .deb..."
    gh release create "$TAG" "$OUT_DEB" \
      --repo "$GITHUB_REPO" \
      --title "${APP_NAME} ${VERSION}" \
      --notes "$NOTES" \
      --latest
  fi

  echo "Release: https://github.com/${GITHUB_REPO}/releases/tag/${TAG}"
  if [[ -n "$BUMP_PART" ]]; then
    echo "Lembre-se de fazer commit/push do bump de versão (${VERSION}) se ainda não estiver no remoto."
  fi
fi

echo ""
echo "Build concluído!"
echo "Instalar localmente:"
echo "  sudo apt install \"./${OUT_DEB#$ROOT/}\""
