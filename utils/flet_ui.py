"""Thread-safe Flet UI updates (Flet 0.70+ / 0.86 desktop).

``FletSocketServer.send_message`` does::

    self.__send_queue.put_nowait(framed)  # asyncio.Queue — not thread-safe

``page.update()`` from a worker or Flet's ThreadPoolExecutor therefore
enqueues a patch that the send loop may not notice until the Flutter
client emits an event (window focus, click, resize). That is the
"UI only refreshes when I alt-tab" bug.

Always hop to ``page.session.connection.loop`` *before* mutating controls
or calling ``page.update()``. Official pattern:

https://github.com/flet-dev/flet/blob/main/sdk/python/examples/cookbook/cpu_bound_callback.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_FLET_NO_CONSOLE_PATCHED = False


def windows_no_window_kwargs() -> dict[str, Any]:
    """Kwargs so Windows subprocesses do not flash a console window."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def patch_flet_desktop_no_console() -> None:
    """Make Flet's desktop client spawn without a console flash on Windows.

    ``flet_desktop.open_flet_view`` / ``open_flet_view_async`` call
    ``Popen`` / ``create_subprocess_exec`` without ``CREATE_NO_WINDOW``.
    Even when ``flet.exe`` is a GUI binary, sibling tooling or stubs can
    still allocate a brief console; this patch suppresses it.
    """
    global _FLET_NO_CONSOLE_PATCHED
    if _FLET_NO_CONSOLE_PATCHED or sys.platform != "win32":
        return
    try:
        import flet_desktop
    except Exception:
        return

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not flags:
        return

    _orig_open = flet_desktop.open_flet_view
    _orig_open_async = flet_desktop.open_flet_view_async
    _orig_popen = subprocess.Popen
    _orig_async_exec = asyncio.create_subprocess_exec

    def _popen_no_console(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | flags
        return _orig_popen(*args, **kwargs)

    async def _async_exec_no_console(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | flags
        return await _orig_async_exec(*args, **kwargs)

    def open_flet_view(page_url, assets_dir, hidden):
        subprocess.Popen = _popen_no_console  # type: ignore[assignment]
        try:
            return _orig_open(page_url, assets_dir, hidden)
        finally:
            subprocess.Popen = _orig_popen  # type: ignore[assignment]

    async def open_flet_view_async(page_url, assets_dir, hidden):
        asyncio.create_subprocess_exec = _async_exec_no_console  # type: ignore[assignment]
        try:
            return await _orig_open_async(page_url, assets_dir, hidden)
        finally:
            asyncio.create_subprocess_exec = _orig_async_exec  # type: ignore[assignment]

    open_flet_view.__wrapped__ = _orig_open  # type: ignore[attr-defined]
    open_flet_view_async.__wrapped__ = _orig_open_async  # type: ignore[attr-defined]
    flet_desktop.open_flet_view = open_flet_view  # type: ignore[assignment]
    flet_desktop.open_flet_view_async = open_flet_view_async  # type: ignore[assignment]
    _FLET_NO_CONSOLE_PATCHED = True


def session_loop(page) -> asyncio.AbstractEventLoop:
    """Return the asyncio loop that owns the Flet session/socket."""
    return page.session.connection.loop


def is_on_session_loop(page) -> bool:
    try:
        return asyncio.get_running_loop() is session_loop(page)
    except RuntimeError:
        return False


def call_on_session_loop(page, fn: Callable[..., Any], *args: Any) -> None:
    """Run ``fn(*args)`` on the session loop (fire-and-forget from workers)."""
    loop = session_loop(page)
    if is_on_session_loop(page):
        fn(*args)
        return
    loop.call_soon_threadsafe(fn, *args)


def run_on_session_loop(
    page,
    coro: Coroutine[Any, Any, T],
    *,
    wait: bool = False,
    timeout: float | None = 10.0,
) -> asyncio.Future | T | None:
    """Schedule a coroutine on the session loop.

    If ``wait`` is true and this is not already the session loop, block
    until the coroutine finishes (deadlocks if called from the loop).
    """
    loop = session_loop(page)
    if is_on_session_loop(page):
        task = asyncio.create_task(coro)
        return None if wait else task
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    if wait:
        return future.result(timeout=timeout)
    return future
