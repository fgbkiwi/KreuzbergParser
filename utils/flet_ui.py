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
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


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
