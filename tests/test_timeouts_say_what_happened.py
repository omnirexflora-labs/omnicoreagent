"""R16 (0.5.0rc1 gate): a timeout says only what is known.

An OTLP export that took longer than export_timeout_seconds was recorded as a
failed export, with TimeoutError's empty message, though the collector
received it five seconds later: the export is given up on, not cancelled. And
an MCP connect timeout printed "Future exception was never retrieved": the
connection's owner set an exception on a future nothing awaited any more.
"""

from __future__ import annotations

import asyncio
import gc

import pytest

from omnicoreagent.core.telemetry.exporters import export_trace_to_many


class SlowExporter:
    name = "otlp"

    async def export_trace(self, trace):
        await asyncio.sleep(1.0)


@pytest.mark.asyncio
async def test_an_export_that_timed_out_says_it_may_have_been_delivered():
    trace = type("T", (), {"trace_id": "trace_x"})()

    (result,) = await export_trace_to_many(trace, [SlowExporter()], timeout=0.05)

    assert result.metadata["error_type"] == "TimeoutError"
    assert "may still have been delivered" in result.metadata["error"]


@pytest.mark.asyncio
async def test_an_mcp_connect_timeout_leaves_no_unretrieved_future():
    from omnicoreagent.mcp_clients_connection.connection import MCPConnectionError, ServerConnection

    async def never_opens(stack):
        await asyncio.sleep(3600)

    unretrieved = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context.get("message")))
    connection = ServerConnection("slow", never_opens)

    with pytest.raises(MCPConnectionError, match="timed out"):
        await connection.open(timeout=0.05)
    await asyncio.sleep(0.05)
    connection = None  # as when the failed connection is dropped: the warning comes at collection
    gc.collect()
    await asyncio.sleep(0)

    assert not [m for m in unretrieved if m and "never retrieved" in m], unretrieved
    loop.set_exception_handler(None)
