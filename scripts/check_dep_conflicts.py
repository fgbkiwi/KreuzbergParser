"""Check KreuzbergParser dependency conflicts in the active environment.

Usage:
    python scripts/check_dep_conflicts.py [cuda_tag] [venv_python]

Exit codes:
    0 — OK (warnings allowed)
    1 — FAIL (conflicts)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Official stack forbids the entire Python Paddle ecosystem; PaddleOCR GPU is
# Kreuzberg native (ort-dynamic wheel) + onnxruntime-gpu only.
paddle_forbidden = {
    "paddleocr",
    "paddlex",
    "paddlepaddle",
    "paddlepaddle-gpu",
    "rapidocr",
}
torch_names = {"torch", "torchvision", "torchaudio"}
linux_only_hints = {
    "nvidia-cufile",
    "nvidia-cufile-cu12",
    "nvidia-cufile-cu13",
}
opencv_names = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)

fails = 0
warns = 0


def emit(level: str, msg: str) -> None:
    global fails, warns
    print(f"{level}: {msg}")
    if level == "FAIL":
        fails += 1
    elif level == "WARN":
        warns += 1


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
            [
                str(python),
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        major, minor = map(int, ver.split("."))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return
    if (major, minor) >= (3, 13):
        emit(
            "FAIL",
            f"Python {ver} in .venv — CUDA wheels often lag; prefer 3.12",
        )


def main() -> int:
    cuda_tag = sys.argv[1] if len(sys.argv) > 1 else "cu130"
    if len(sys.argv) > 2:
        venv_python = Path(sys.argv[2])
    else:
        root = Path(__file__).resolve().parent.parent
        candidates = [
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        ]
        venv_python = next((p for p in candidates if p.is_file()), candidates[0])

    installed = installed_versions(venv_python)

    if cuda_tag != "cpu":
        for name in torch_names:
            ver = installed.get(name)
            if not ver:
                continue
            lower = ver.lower()
            if "+cpu" in lower or (
                name == "torch" and "+cu" not in lower and "cu" not in lower
            ):
                emit(
                    "FAIL",
                    f"installed {name}={ver} looks like PyPI/CPU — expected CUDA build "
                    f"(+{cuda_tag}) from the PyTorch index",
                )
            elif name == "torch" and f"+{cuda_tag}" not in lower and "+cu" in lower:
                emit(
                    "WARN",
                    f"installed {name}={ver} CUDA tag differs from expected +{cuda_tag}",
                )

    for name in sorted(paddle_forbidden):
        if name in installed:
            emit(
                "FAIL",
                f"installed package {name} — uninstall; PaddleOCR GPU uses Kreuzberg "
                "ort-dynamic + onnxruntime-gpu only",
            )

    for name in sorted(linux_only_hints):
        if name in installed:
            emit(
                "FAIL",
                f"installed Linux-only package {name} — remove it on Windows",
            )

    opencv_present = sorted({name for name in opencv_names if name in installed})
    if len(opencv_present) > 1:
        emit(
            "FAIL",
            "multiple OpenCV packages provide cv2 "
            f"({', '.join(opencv_present)}) — keep only one",
        )
    if "opencv-python" in opencv_present:
        emit("WARN", "opencv-python (GUI) present — prefer opencv-python-headless")
    if any(name.startswith("opencv-contrib") for name in opencv_present):
        emit(
            "WARN",
            "opencv-contrib-* present — prefer opencv-python-headless only",
        )

    check_python_version(venv_python)

    if fails:
        print(f"==> Conflict check: {fails} FAIL, {warns} WARN")
        return 1
    if warns:
        print(f"==> Conflict check: OK with {warns} WARN")
    else:
        print("==> Conflict check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
