"""One MCP server connection, owned by a single task.

The MCP SDK's transports are anyio context managers whose cancel scopes must
be entered and exited by the same task. A dedicated owner task therefore
opens the transport and session, holds them while the connection is in use,
and closes them when asked. Callers only talk to the session.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from omnicoreagent.core.logging import logger

# Opens the transport and session on the given stack and returns what the
# connection exposes (session, tools, transport type, reported identity).
Opener = Callable[[AsyncExitStack], Awaitable[dict[str, Any]]]

CLOSE_TIMEOUT_SECONDS = 10.0


class MCPConnectionError(Exception):
    """A connection could not be opened."""


class ServerConnection:
    def __init__(
        self,
        name: str,
        opener: Opener,
        on_lost: Callable[[ServerConnection, BaseException], None] | None = None,
    ) -> None:
        self.name = name
        self._opener = opener
        self._on_lost = on_lost
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._opened: asyncio.Future[dict[str, Any]] | None = None
        # Why the connection failed or ended while in use.
        self.error: BaseException | None = None
        # An error raised while closing on request.
        self.close_error: BaseException | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def open(self, timeout: float) -> dict[str, Any]:
        """Start the owner task and wait until the connection is usable."""
        loop = asyncio.get_running_loop()
        self._opened = loop.create_future()
        self._task = asyncio.create_task(self._own(), name=f"mcp-server:{self.name}")
        try:
            return await asyncio.wait_for(asyncio.shield(self._opened), timeout)
        except asyncio.TimeoutError:
            await self._abandon()
            raise MCPConnectionError(
                f"Connecting to MCP server '{self.name}' timed out after {timeout:g}s"
            ) from None
        except asyncio.CancelledError:
            await self._abandon()
            raise

    async def _abandon(self) -> None:
        """Stop a connection that never became usable.

        It has nothing to close gracefully, and a task stuck in the handshake
        never sees the stop signal, so the owner task is cancelled; it unwinds
        its own contexts.
        """
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> BaseException | None:
        """Ask the owner task to close the connection; return any close error."""
        task = self._task
        if task is None or task.done():
            return self._close_error(task)
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), CLOSE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            # The server ignored the close; stop waiting on it.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except BaseException:
            pass
        return self._close_error(task)

    async def _own(self) -> None:
        opened = self._opened
        try:
            async with AsyncExitStack() as stack:
                exposed = await self._opener(stack)
                if opened is not None and not opened.done():
                    opened.set_result(exposed)
                await self._stop.wait()
        except asyncio.CancelledError:
            if opened is not None and not opened.done():
                opened.set_exception(MCPConnectionError(f"MCP server '{self.name}' was cancelled"))
            raise
        except BaseException as exc:  # noqa: BLE001 - recorded, never lost
            if self._stop.is_set():
                self.close_error = exc
                return
            self.error = exc
            if opened is not None and not opened.done():
                opened.set_exception(exc)
            else:
                # The connection died while in use (the server exited).
                logger.warning(f"MCP server '{self.name}' connection ended: {exc!r}")
                if self._on_lost is not None:
                    self._on_lost(self, exc)

    def _close_error(self, task: asyncio.Task | None) -> BaseException | None:
        if task is None or task.cancelled():
            return None
        return self.close_error
