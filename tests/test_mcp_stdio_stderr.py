"""A stdio MCP server starts whatever the process did to its own stderr.

The MCP client binds ``sys.stderr`` as the server's error log when it is
imported. In a notebook, or a test that captures output, that object has no
file descriptor, and every stdio server then failed to start with
``io.UnsupportedOperation: fileno`` — found when the trajectory acceptance
ran alone under pytest's ``capsys``. The transport now hands the server a
log it can inherit: the real stderr when it has one, else nothing.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from omnicoreagent.mcp_clients_connection import transports
from omnicoreagent.mcp_clients_connection.client import MCPClient

PROBE = str(Path(__file__).parent / "fixtures" / "mcp_probe_server.py")


def test_the_server_log_is_a_real_file_or_nothing(monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert transports.server_errlog() in (sys.__stderr__, subprocess.DEVNULL)

    monkeypatch.setattr(sys, "stderr", sys.__stderr__)
    assert transports.server_errlog() is sys.__stderr__


@pytest.mark.asyncio
async def test_a_stdio_server_starts_when_stderr_has_no_descriptor(monkeypatch):
    from mcp.client import stdio

    # What a notebook or an output-capturing test leaves the client with.
    monkeypatch.setattr(stdio.stdio_client.__wrapped__, "__defaults__", (io.StringIO(),))
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    client = MCPClient(
        servers=[{"name": "probe", "transport_type": "stdio", "command": sys.executable, "args": [PROBE, "stdio", "stderr-test"]}]
    )
    await client.connect_to_servers()
    try:
        assert "probe" in client.sessions
    finally:
        await client.cleanup()
