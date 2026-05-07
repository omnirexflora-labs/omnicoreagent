import asyncio
import traceback
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from omnicoreagent.core.logging import logger


class BackgroundTaskManager:
    """Run background, async, or blocking tasks safely for an agent."""

    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.tasks = set()

    def run_background(self, func: Callable[..., Any], *args, **kwargs):
        """Run a synchronous function in a background thread."""

        def wrapper():
            try:
                func(*args, **kwargs)
            except Exception:
                traceback.print_exc()

        asyncio.create_task(asyncio.to_thread(wrapper))

    def run_background_async(self, coro: Coroutine):
        """Run an async coroutine in the current event loop."""

        async def runner():
            try:
                await coro
            except Exception:
                traceback.print_exc()

        asyncio.create_task(runner())

    def run_background_strict(self, coro):
        """Fire and forget a coroutine safely, with internal error handling."""
        if asyncio.iscoroutine(coro):
            task = asyncio.create_task(self._run_safe(coro))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        else:
            logger.warning("Tried to run non-coroutine task: %s", coro)

    async def _run_safe(self, coro):
        try:
            await coro
        except asyncio.CancelledError:
            logger.debug("Background task cancelled.")
        except Exception as e:
            logger.exception("Background task failed: %s", e)

    def run_in_executor(self, func: Callable[..., Any], *args, **kwargs):
        """Run a blocking function in the thread pool and return an awaitable."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(self.executor, lambda: func(*args, **kwargs))
