#!/usr/bin/env bash
# Update project dependencies with uv, resolving a conflict-free pin set.
#
# Everything resolves from PyPI. The GPU stack is onnxruntime-gpu[cuda,cudnn],
# whose extras pull the CUDA 13 / cuDNN 9 nvidia-* wheels; there is no PyTorch.
#
# Usage:
#   ./scripts/update_deps.sh                 # upgrade + write requirements.txt
#   ./scripts/update_deps.sh --dry-run       # resolve only, do not write
#   ./scripts/update_deps.sh --check         # resolve in temp, compare, do not write
#   ./scripts/update_deps.sh --sync          # also install into .venv
#   ./scripts/update_deps.sh --no-upgrade    # re-resolve without upgrading
#   ./scripts/update_deps.sh --python 3.12   # target Python version
#
# See docs/DEPENDENCY_CONFLICTS.md for conflict rules.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IN_FILE="${ROOT}/requirements.in"
OUT_FILE="${ROOT}/requirements.txt"

DO_UPGRADE=1
DO_SYNC=0
DRY_RUN=0
CHECK_ONLY=0
PYTHON_VERSION=""
CUSTOM_COMPILE_CMD="./scripts/update_deps.sh"

TMP_DIR=""
cleanup() {
  [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]] && rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() {
  echo "error: $*" >&2
  exit 1
}

require_uv() {
  command -v uv >/dev/null 2>&1 || die "uv not found. Install: https://docs.astral.sh/uv/"
}

# Report conflicts in a requirements body and/or the active environment.
# Prints WARN/FAIL lines; returns 0 if clean, 1 if any FAIL.
check_conflicts() {
  local req_file="$1"
  python3 - "$req_file" "${ROOT}/.venv/bin/python" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

req_path = Path(sys.argv[1])
venv_python = Path(sys.argv[2]) if len(sys.argv) > 2 else None

req_re = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)\s*(?P<op>[<>=!~]=?|@)\s*(?P<ver>.+)$"
)
# PaddleOCR roda exclusivamente pelo Kreuzberg nativo (wheel local ort-dynamic);
# nenhum pacote Python do ecossistema Paddle deve existir no ambiente.
paddle_forbidden = {
    "paddlepaddle",
    "paddlepaddle-gpu",
    "paddleocr",
    "paddlex",
    "easyocr",
}
# TrOCR foi removido; o stack PyTorch (~3.5 GB) só inflaria o instalador.
torch_stack = {"torch", "torchvision", "torchaudio", "triton", "transformers"}
fails = 0
warns = 0


def emit(level: str, msg: str) -> None:
    global fails, warns
    print(f"{level}: {msg}")
    if level == "FAIL":
        fails += 1
    elif level == "WARN":
        warns += 1


def parse_reqs(path: Path) -> dict[str, str]:
    pkgs: dict[str, str] = {}
    if not path.is_file():
        return pkgs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        m = req_re.match(line)
        if not m:
            continue
        key = m.group("name").lower().replace("_", "-")
        pkgs[key] = m.group("ver").strip()
    return pkgs


def installed_versions(python: Path) -> dict[str, str]:
    if not python.is_file():
        return {}
    cmds = [
        ["uv", "pip", "freeze", "--python", str(python)],
        [str(python), "-m", "pip", "list", "--format=freeze"],
    ]
    out = ""
    for cmd in cmds:
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            break
        except (OSError, subprocess.CalledProcessError):
            continue
    if not out:
        return {}
    pkgs: dict[str, str] = {}
    for line in out.splitlines():
        if "==" not in line:
            continue
        name, ver = line.split("==", 1)
        pkgs[name.lower().replace("_", "-")] = ver.strip()
    return pkgs


def check_python_version(python: Path) -> None:
    if not python.is_file():
        return
    try:
        ver = subprocess.check_output(
            [str(python), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        major, minor = map(int, ver.split("."))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return
    if (major, minor) >= (3, 13):
        emit(
            "FAIL",
            f"Python {ver} in .venv — CUDA wheels often lag; prefer 3.12 "
            "(update_deps resolves for 3.12 automatically)",
        )


pinned = parse_reqs(req_path)
installed = installed_versions(venv_python) if venv_python else {}

for name in sorted(torch_stack):
    if name in pinned:
        emit(
            "FAIL",
            f"requirements pin {name} — o app não usa PyTorch; algo em "
            "requirements.in voltou a puxá-lo",
        )
    if name in installed:
        emit(
            "WARN",
            f"installed package {name} — não usado pelo app; --sync o remove",
        )

for name in sorted(paddle_forbidden):
    if name in pinned:
        emit(
            "FAIL",
            f"requirements pin {name} — PaddleOCR GPU usa somente o Kreuzberg "
            "nativo (wheel ort-dynamic) + onnxruntime-gpu",
        )
    if name in installed:
        emit(
            "FAIL",
            f"installed package {name} — desinstale; PaddleOCR GPU usa somente "
            "o Kreuzberg nativo + onnxruntime-gpu",
        )

# OpenCV: exactly one cv2 provider (opencv-python-headless, via RapidOCR).
opencv_names = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)
opencv_present = sorted(
    {
        name
        for name in opencv_names
        if name in pinned or name in installed
    }
)
if len(opencv_present) > 1:
    emit(
        "FAIL",
        "multiple OpenCV packages provide cv2 "
        f"({', '.join(opencv_present)}) — keep only one",
    )
if "opencv-python" in opencv_present:
    emit("WARN", "opencv-python (GUI) present — prefer opencv-python-headless")

check_python_version(venv_python) if venv_python else None

if fails:
    print(f"==> Conflict check: {fails} FAIL, {warns} WARN")
    sys.exit(1)
if warns:
    print(f"==> Conflict check: OK with {warns} WARN")
else:
    print("==> Conflict check: OK")
sys.exit(0)
PY
}

# Compare resolved pins with requirements.txt (and optionally installed).
# Exit 0 if identical; 1 if drift (advisory for --check).
compare_resolved() {
  local resolved="$1"
  local current="$2"
  python3 - "$resolved" "$current" <<'PY'
import re
import sys
from pathlib import Path

req_re = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)\s*(?P<op>[<>=!~]=?|@)\s*(?P<ver>.+)$"
)


def parse(path: Path) -> dict[str, str]:
    pkgs: dict[str, str] = {}
    if not path.is_file():
        return pkgs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        m = req_re.match(line)
        if not m:
            continue
        key = m.group("name").lower().replace("_", "-")
        pkgs[key] = m.group("ver").strip()
    return pkgs


resolved = parse(Path(sys.argv[1]))
current = parse(Path(sys.argv[2]))
if not current:
    print("WARN: requirements.txt missing or empty — cannot compare pins")
    sys.exit(1)

only_new = sorted(set(resolved) - set(current))
only_old = sorted(set(current) - set(resolved))
changed = sorted(
    k for k in set(resolved) & set(current) if resolved[k] != current[k]
)

if not only_new and not only_old and not changed:
    print("==> Pins match requirements.txt")
    sys.exit(0)

print("==> Drift vs requirements.txt:")
for k in changed[:40]:
    print(f"  ~ {k}: {current[k]} -> {resolved[k]}")
if len(changed) > 40:
    print(f"  ... and {len(changed) - 40} more version changes")
for k in only_new[:20]:
    print(f"  + {k}=={resolved[k]}")
for k in only_old[:20]:
    print(f"  - {k}=={current[k]}")
sys.exit(1)
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --sync) DO_SYNC=1; shift ;;
    --no-upgrade) DO_UPGRADE=0; shift ;;
    --upgrade) DO_UPGRADE=1; shift ;;
    --python)
      [[ $# -ge 2 ]] || die "--python requires a value"
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --in)
      [[ $# -ge 2 ]] || die "--in requires a path"
      IN_FILE="$2"
      shift 2
      ;;
    --out)
      [[ $# -ge 2 ]] || die "--out requires a path"
      OUT_FILE="$2"
      shift 2
      ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [[ "$CHECK_ONLY" -eq 1 && "$DRY_RUN" -eq 1 ]]; then
  die "--check and --dry-run are mutually exclusive"
fi
if [[ "$CHECK_ONLY" -eq 1 && "$DO_SYNC" -eq 1 ]]; then
  die "--check does not install; omit --sync"
fi

require_uv
[[ -f "$IN_FILE" ]] || die "missing input file: $IN_FILE"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/update_deps.XXXXXX")"
EXCLUDES="${TMP_DIR}/excludes.txt"
FINAL_OUT="${TMP_DIR}/final.txt"

# RapidOCR depends on opencv-python; requirements.in supplies the single cv2
# provider (opencv-python-headless), so the GUI/contrib wheels are excluded.
cat > "$EXCLUDES" <<'EOF'
opencv-python
opencv-contrib-python
opencv-contrib-python-headless
EOF

PYTHON_ARGS=()
if [[ -n "$PYTHON_VERSION" ]]; then
  PYTHON_ARGS+=(--python-version "$PYTHON_VERSION")
  echo "==> Using Python ${PYTHON_VERSION}"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  VENV_PY_VER="$("${ROOT}/.venv/bin/python" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  VENV_PY_MINOR="$("${ROOT}/.venv/bin/python" -c 'import sys; print(sys.version_info[1])')"
  # The installers bundle CPython 3.12; resolve for it.
  if [[ "${VENV_PY_MINOR}" -ge 13 ]]; then
    PYTHON_VERSION="3.12"
    PYTHON_ARGS+=(--python-version "$PYTHON_VERSION")
    echo "==> .venv is Python ${VENV_PY_VER}; resolving for 3.12 (bundled runtime)"
    echo "    Override with: --python ${VENV_PY_VER}"
  else
    PYTHON_ARGS+=(--python "${ROOT}/.venv/bin/python")
    echo "==> Using interpreter ${ROOT}/.venv/bin/python (${VENV_PY_VER})"
  fi
fi

UPGRADE_ARGS=()
if [[ "$DO_UPGRADE" -eq 1 ]]; then
  UPGRADE_ARGS+=(--upgrade)
fi

echo "==> Resolving dependencies with uv"
uv pip compile "$IN_FILE" \
  --output-file "$FINAL_OUT" \
  --excludes "$EXCLUDES" \
  --quiet \
  --no-strip-extras \
  --annotation-style split \
  --custom-compile-command "$CUSTOM_COMPILE_CMD" \
  "${PYTHON_ARGS[@]}" \
  "${UPGRADE_ARGS[@]}"

show_direct() {
  local file="$1"
  echo "Resolved direct dependencies:"
  while IFS= read -r pkg || [[ -n "$pkg" ]]; do
    trimmed="${pkg%%#*}"
    trimmed="$(echo "$trimmed" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$trimmed" || "$trimmed" == --* ]] && continue
    name="${trimmed%%[<>=!~ ]*}"
    name="${name%%\[*}"
    pattern="$(echo "$name" | tr '[:upper:]' '[:lower:]' | sed 's/[.]/\\./g')"
    match="$(grep -iE "^${pattern}(\[[^]]*\])?(==| @ )" "$file" || true)"
    if [[ -n "$match" ]]; then
      echo "  $match"
    else
      echo "  ${name}: (not found in compile output)"
    fi
  done < "$IN_FILE"
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "==> Check mode (not writing $OUT_FILE)"
  echo
  show_direct "$FINAL_OUT"
  echo
  CONFLICT_RC=0
  # Current pins + installed env
  if [[ -f "$OUT_FILE" ]]; then
    check_conflicts "$OUT_FILE" || CONFLICT_RC=1
  else
    echo "WARN: $OUT_FILE missing; checking resolved set only"
    check_conflicts "$FINAL_OUT" || CONFLICT_RC=1
  fi
  DRIFT_RC=0
  compare_resolved "$FINAL_OUT" "$OUT_FILE" || DRIFT_RC=1
  if [[ "$CONFLICT_RC" -ne 0 ]]; then
    echo "==> Check FAILED (dependency conflicts)"
    echo "    See docs/DEPENDENCY_CONFLICTS.md"
    exit 1
  fi
  if [[ "$DRIFT_RC" -ne 0 ]]; then
    echo "==> Check: newer pins available (advisory; re-run without --check to update)"
    exit 2
  fi
  echo "==> Check OK (no conflicts; requirements.txt up to date)"
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> Dry run (not writing $OUT_FILE)"
  echo
  show_direct "$FINAL_OUT"
  exit 0
fi

cp "$FINAL_OUT" "$OUT_FILE"
echo "==> Wrote conflict-free pins to $OUT_FILE"
show_direct "$OUT_FILE"

if [[ "$DO_SYNC" -eq 1 ]]; then
  echo "==> Syncing environment from $OUT_FILE"
  SYNC_ARGS=(pip sync "$OUT_FILE" --quiet)
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    SYNC_ARGS+=(--python "${ROOT}/.venv/bin/python")
  elif [[ -n "$PYTHON_VERSION" ]]; then
    SYNC_ARGS+=(--python-version "$PYTHON_VERSION")
  fi
  uv "${SYNC_ARGS[@]}"
  echo "==> Environment synced"

  # Removing a second OpenCV wheel deletes files shared with the one that
  # stays, leaving an empty namespace cv2/. Reinstall the headless provider.
  if [[ -x "${ROOT}/.venv/bin/python" ]] && \
     ! "${ROOT}/.venv/bin/python" -c "import cv2; cv2.imread" >/dev/null 2>&1; then
    echo "==> Repairing cv2 (reinstalling opencv-python-headless)"
    CV2_PIN="$(grep -iE '^opencv-python-headless==' "$OUT_FILE" || echo opencv-python-headless)"
    uv pip install --quiet --python "${ROOT}/.venv/bin/python" \
      --reinstall --no-deps "$CV2_PIN"
  fi

  # PaddleOCR GPU: o sync instala o kreuzberg do PyPI (ONNX Runtime só-CPU).
  # Reinstala por cima o wheel local ort-dynamic, que honra ORT_DYLIB_PATH e
  # habilita CUDA no PaddleOCR nativo (ver scripts/build_kreuzberg_gpu.sh).
  GPU_WHEEL="$(ls -1 "${ROOT}"/vendor/wheels/kreuzberg-*.whl 2>/dev/null | sort | tail -n 1 || true)"
  if [[ -n "$GPU_WHEEL" ]]; then
    echo "==> Reinstalando wheel GPU local do Kreuzberg: ${GPU_WHEEL##*/}"
    uv pip install --quiet --python "${ROOT}/.venv/bin/python" \
      --force-reinstall --no-deps "$GPU_WHEEL"
  else
    echo "WARN: vendor/wheels/kreuzberg-*.whl não encontrado."
    echo "      O kreuzberg do PyPI NÃO roda PaddleOCR em CUDA."
    echo "      Gere o wheel GPU com: ./scripts/build_kreuzberg_gpu.sh"
  fi
fi

echo "==> Done"
echo "    Review: $OUT_FILE"
echo "    Source: $IN_FILE"
