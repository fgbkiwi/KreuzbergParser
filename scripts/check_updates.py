#!/usr/bin/env python3
"""Periodic, non-blocking dependency update advisory.

Checks at most every N days (default 7) using logs/.last_dep_check.
Invokes scripts/update_deps.sh --check (resolve in temp, compare, no write).
Never auto-upgrades. Safe to call from main.py:

    from scripts.check_updates import maybe_check_dependency_updates
    maybe_check_dependency_updates(logger_=logger, background=True)

Or as CLI:

    python scripts/check_updates.py
    python scripts/check_updates.py --force
    python scripts/check_updates.py --days 14
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MARKER = ROOT / "logs" / ".last_dep_check"
DEFAULT_DAYS = 7
UPDATE_SCRIPT = ROOT / "scripts" / "update_deps.sh"

logger = logging.getLogger(__name__)


def _read_last_check(marker: Path) -> Optional[float]:
    try:
        return float(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_last_check(marker: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{time.time():.0f}\n", encoding="utf-8")


def should_run_check(marker: Path = DEFAULT_MARKER, interval_days: float = DEFAULT_DAYS) -> bool:
    """Return True if enough days have passed since the last check."""
    if interval_days <= 0:
        return True
    last = _read_last_check(marker)
    if last is None:
        return True
    return (time.time() - last) >= interval_days * 86400


def run_dependency_check(
    *,
    timeout_s: float = 600.0,
    cuda_tag: str = "cu130",
) -> subprocess.CompletedProcess[str]:
    """Run update_deps.sh --check. Does not write requirements.txt."""
    if not UPDATE_SCRIPT.is_file():
        raise FileNotFoundError(f"missing {UPDATE_SCRIPT}")
    cmd = [
        "bash",
        str(UPDATE_SCRIPT),
        "--check",
        "--cuda",
        cuda_tag,
    ]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=os.environ.copy(),
        check=False,
    )


def _run_check_body(
    *,
    log: logging.Logger,
    marker: Path,
    cuda_tag: str,
    timeout_s: float,
) -> Optional[int]:
    log.info(
        "Verificando atualizações de dependências (aviso apenas; não instala)..."
    )
    try:
        result = run_dependency_check(timeout_s=timeout_s, cuda_tag=cuda_tag)
    except FileNotFoundError as exc:
        log.warning("Checagem de deps ignorada: %s", exc)
        return None
    except subprocess.TimeoutExpired:
        log.warning(
            "Checagem de deps excedeu o tempo limite (%.0fs); ignorando.",
            timeout_s,
        )
        _write_last_check(marker)
        return None
    except OSError as exc:
        log.warning("Checagem de deps falhou ao iniciar: %s", exc)
        return None

    # Stamp even on failure so we do not hammer the network every startup.
    _write_last_check(marker)

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if out:
        for line in out.splitlines()[-30:]:
            log.info("[deps] %s", line)
    if err:
        for line in err.splitlines()[-15:]:
            log.warning("[deps] %s", line)

    rc = result.returncode
    if rc == 0:
        log.info("Dependências: sem conflitos; pins atualizados.")
    elif rc == 2:
        log.warning(
            "Dependências: há pins mais novos disponíveis. "
            "Revise com ./scripts/update_deps.sh --check e atualize com "
            "./scripts/update_deps.sh (ver docs/DEPENDENCY_CONFLICTS.md)."
        )
    elif rc == 1:
        log.warning(
            "Dependências: conflitos detectados. "
            "Veja docs/DEPENDENCY_CONFLICTS.md e ./scripts/update_deps.sh --check."
        )
    else:
        log.warning("Checagem de deps terminou com código %s.", rc)
    return rc


def maybe_check_dependency_updates(
    *,
    logger_: Optional[logging.Logger] = None,
    interval_days: float = DEFAULT_DAYS,
    force: bool = False,
    marker: Path = DEFAULT_MARKER,
    cuda_tag: str = "cu130",
    timeout_s: float = 600.0,
    background: bool = False,
) -> Optional[int]:
    """
    Advisory check; never raises to the caller for expected failures.

    Returns the update_deps.sh exit code when a check ran synchronously,
    None if skipped or started in a background thread.
    Exit codes from --check: 0=ok, 1=conflicts, 2=drift (updates available).
    """
    log = logger_ or logger
    if not force and not should_run_check(marker, interval_days):
        log.debug(
            "Dependency check skipped (last run within %.0f days: %s)",
            interval_days,
            marker,
        )
        return None

    if background:
        thread = threading.Thread(
            target=_run_check_body,
            kwargs={
                "log": log,
                "marker": marker,
                "cuda_tag": cuda_tag,
                "timeout_s": timeout_s,
            },
            name="dep-update-check",
            daemon=True,
        )
        thread.start()
        log.debug("Checagem de deps iniciada em background")
        return None

    return _run_check_body(
        log=log,
        marker=marker,
        cuda_tag=cuda_tag,
        timeout_s=timeout_s,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advisory dependency update check (no auto-upgrade)."
    )
    parser.add_argument(
        "--days",
        type=float,
        default=DEFAULT_DAYS,
        help=f"Minimum days between checks (default {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the last-check marker and run now",
    )
    parser.add_argument(
        "--cuda",
        default="cu130",
        help="PyTorch CUDA tag passed to update_deps.sh (default cu130)",
    )
    parser.add_argument(
        "--marker",
        type=Path,
        default=DEFAULT_MARKER,
        help="Path to last-check timestamp file",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    rc = maybe_check_dependency_updates(
        interval_days=args.days,
        force=args.force,
        marker=args.marker,
        cuda_tag=args.cuda,
    )
    if rc is None:
        print("Skipped (within interval). Use --force to run now.")
        return 0
    # Advisory CLI: conflicts/drift still exit 0 so hooks stay non-blocking.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
