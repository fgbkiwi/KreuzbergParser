"""
Impede a suspensão do sistema enquanto uma fila de conversão está rodando.

Linux: segura um lock ``sleep`` do systemd-logind via ``systemd-inhibit``.
O comando bloqueado é ``tail --pid=<nosso pid>``, então o lock some sozinho
se o app morrer sem chamar ``release()``.

Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)``.
O estado vale para a thread que chamou; use o context manager na mesma
thread que faz o trabalho.

A tela ainda pode apagar/bloquear normalmente — só a suspensão é bloqueada.
Falhas aqui nunca interrompem o processamento.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class SleepInhibitor:
    """Context manager que bloqueia a suspensão enquanto estiver ativo."""

    def __init__(self, why: str = "Conversão de PDFs em andamento") -> None:
        self.why = why
        self._proc: subprocess.Popen | None = None
        self._win_active = False

    @property
    def active(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        return self._win_active

    def acquire(self) -> bool:
        if self.active:
            return True
        try:
            if sys.platform == "win32":
                return self._acquire_windows()
            if sys.platform.startswith("linux"):
                return self._acquire_linux()
        except Exception:
            logger.warning("Não foi possível bloquear a suspensão", exc_info=True)
        return False

    def release(self) -> None:
        try:
            if self._proc is not None:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                logger.info("Suspensão do sistema liberada")
            if self._win_active:
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
                logger.info("Suspensão do sistema liberada")
        except Exception:
            logger.debug("Falha ao liberar bloqueio de suspensão", exc_info=True)
        finally:
            self._proc = None
            self._win_active = False

    def __enter__(self) -> "SleepInhibitor":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def _acquire_linux(self) -> bool:
        inhibit = shutil.which("systemd-inhibit")
        tail = shutil.which("tail")
        if not inhibit or not tail:
            logger.info("systemd-inhibit indisponível; suspensão não bloqueada")
            return False
        self._proc = subprocess.Popen(
            [
                inhibit,
                "--what=sleep",
                "--mode=block",
                "--who=KiwiDown",
                f"--why={self.why}",
                tail,
                f"--pid={os.getpid()}",
                "-f",
                "/dev/null",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            # Se falhar (ex.: sem logind), sai quase imediatamente.
            _, err = self._proc.communicate(timeout=0.5)
            logger.warning(
                "systemd-inhibit falhou: %s",
                (err or b"").decode(errors="replace").strip(),
            )
            self._proc = None
            return False
        except subprocess.TimeoutExpired:
            pass
        logger.info("Suspensão do sistema bloqueada durante a fila")
        return True

    def _acquire_windows(self) -> bool:
        import ctypes

        prev = ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
        if not prev:
            logger.warning("SetThreadExecutionState falhou")
            return False
        self._win_active = True
        logger.info("Suspensão do sistema bloqueada durante a fila")
        return True
