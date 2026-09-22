"""MCP connection lifecycle and errors against real MCP 2 servers.

Each stdio server is tagged with a unique argument so a test can find (and
kill) exactly its own process and prove nothing is left running.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.mcp_clients_connection.client import MCPClient

FIXTURES = Path(__file__).parent / "fixtures"
LIFECYCLE = str(FIXTURES / "mcp_lifecycle_server.py")
PROBE = str(FIXTURES / "mcp_probe_server.py")


def _server(name: str, script: str = LIFECYCLE, **extra) -> dict:
    tag = f"--test-tag={uuid.uuid4().hex}"
    return {
        "name": name,
        "transport_type": "stdio",
        "command": sys.executable,
        "args": [script, "stdio", tag] if script == PROBE else [script, tag],
        **extra,
    }


def _running(server: dict) -> list[int]:
    tag = server["args"][-1]
    found = subprocess.run(["pgrep", "-f", "--", tag], capture_output=True, text=True)
    return [int(pid) for pid in found.stdout.split()]


async def _gone(server: dict, timeout: float = 5) -> bool:
    for _ in range(int(timeout / 0.1)):
        if not _running(server):
            return True
        await asyncio.sleep(0.1)
    return False


async def _call(client: MCPClient, name: str, tool: str, args: dict | None = None) -> dict:
    handler = MCPToolHandler(sessions=client.sessions, server_name=name)
    # Bounded so a regression fails instead of hanging the suite.
    return await asyncio.wait_for(ToolExecutor(handler).execute(tool, args or {}), 30)


async def _connect(client: MCPClient) -> None:
    await asyncio.wait_for(client.connect_to_servers(), 30)


@pytest.mark.asyncio
async def test_cleanup_closes_in_the_owner_task_and_leaves_no_process(caplog):
    servers = [_server("one", PROBE), _server("two")]
    client = MCPClient(servers=servers)
    await _connect(client)
    assert sorted(client.sessions) == ["one", "two"]
    assert all(_running(server) for server in servers)

    with caplog.at_level(logging.WARNING, logger="omnicoreagent"):
        await client.cleanup()

    assert not [r for r in caplog.records if "cancel scope" in r.getMessage().lower()]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    for server in servers:
        assert await _gone(server), f"{server['name']} still running"


@pytest.mark.asyncio
async def test_tool_listing_follows_every_page():
    client = MCPClient(servers=[_server("paged")])
    await _connect(client)
    try:
        names = [tool.name for tool in client.available_tools["paged"]]
    finally:
        await client.cleanup()

    assert names == ["echo", "pid", "slow", "protocol_error", "page_three_tool"]


@pytest.mark.asyncio
async def test_a_protocol_error_is_an_error_result_with_its_code():
    client = MCPClient(servers=[_server("paged")])
    await _connect(client)
    try:
        result = await _call(client, "paged", "protocol_error")
    finally:
        await client.cleanup()

    assert result["status"] == "error"
    assert result["data"]["mcp_error"]["code"] == -32602
    assert "the server rejected this request" in result["message"]


@pytest.mark.asyncio
async def test_a_configured_call_timeout_is_an_error_with_the_timeout_code():
    client = MCPClient(servers=[_server("paged", call_timeout=1.0)])
    await _connect(client)
    try:
        result = await _call(client, "paged", "slow")
        after = await _call(client, "paged", "echo", {"text": "still here"})
    finally:
        await client.cleanup()

    assert result["status"] == "error"
    assert result["data"]["mcp_error"]["code"] == -32001
    assert after["data"] == "still here"


@pytest.mark.asyncio
async def test_a_dropped_session_reconnects_once_and_the_call_succeeds():
    server = _server("paged")
    client = MCPClient(servers=[server])
    await _connect(client)
    try:
        [first_pid] = _running(server)
        os.kill(first_pid, signal.SIGKILL)
        await asyncio.sleep(0.3)

        result = await _call(client, "paged", "echo", {"text": "after restart"})
        # Snapshot: cleanup below marks the live entry disconnected.
        info = dict(client.sessions["paged"])
        [second_pid] = _running(server)
    finally:
        await client.cleanup()

    assert result["status"] == "success"
    assert result["data"] == "after restart"
    assert second_pid != first_pid
    assert info["connected"] is True
    assert info["reconnects"] == 1
    assert await _gone(server)


@pytest.mark.asyncio
async def test_a_failed_reconnect_is_reported_and_the_server_marked_unavailable(monkeypatch):
    server = _server("paged")
    client = MCPClient(servers=[server])
    await _connect(client)
    try:
        [pid] = _running(server)
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.3)
        # The server can no longer be started.
        client.servers[0]["command"] = "/nonexistent/mcp-server"

        result = await _call(client, "paged", "echo", {"text": "x"})
        # Snapshot: cleanup below marks the live entry disconnected.
        info = dict(client.sessions["paged"])
    finally:
        await client.cleanup()

    assert result["status"] == "error"
    assert "reconnect failed" in result["message"].lower()
    assert result["data"]["mcp_error"]["code"] == -32000
    assert info["connected"] is False
    assert info["last_error"]


@pytest.mark.asyncio
async def test_a_server_that_never_answers_times_out_without_stopping_the_others():
    silent = {
        "name": "silent",
        "transport_type": "stdio",
        "command": sys.executable,
        "args": ["-c", "import time; time.sleep(60)", f"--test-tag={uuid.uuid4().hex}"],
        "connect_timeout": 2,
    }
    good = _server("good")
    client = MCPClient(servers=[silent, good])

    started = asyncio.get_running_loop().time()
    await _connect(client)
    elapsed = asyncio.get_running_loop().time() - started
    try:
        assert list(client.sessions) == ["good"]
        failure = client.state.failures["silent"]
        assert "timed out" in failure["error"].lower()
        assert elapsed < 10
    finally:
        await client.cleanup()

    assert await _gone(silent)
    assert await _gone(good)


@pytest.mark.asyncio
async def test_removing_a_server_stops_it_and_keeps_the_others():
    removed, kept = _server("removed"), _server("kept", PROBE)
    client = MCPClient(servers=[removed, kept])
    await _connect(client)
    try:
        message = await client.remove_server("removed")
        assert message == "removed disconnected successfully"
        assert list(client.sessions) == ["kept"]
        assert await _gone(removed)
        still = await _call(client, "kept", "echo", {"text": "still here"})
        assert still["data"] == "still here"
    finally:
        await client.cleanup()
    assert await _gone(kept)


def test_agent_config_carries_the_connection_timeouts():
    from omnicoreagent.core.runtime.config import normalize_mcp_tools

    [default, custom] = normalize_mcp_tools(
        [
            {"name": "default", "command": "server"},
            {"name": "custom", "command": "server", "connect_timeout": 5, "call_timeout": 2.5},
        ]
    )

    assert default["connect_timeout"] == 30.0
    assert "call_timeout" not in default
    assert (custom["connect_timeout"], custom["call_timeout"]) == (5, 2.5)


def _start_http_probe(port: int) -> subprocess.Popen:
    import socket
    import time

    process = subprocess.Popen(
        [sys.executable, PROBE, "streamable-http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return process
        except OSError:
            time.sleep(0.1)
    process.kill()
    raise TimeoutError("probe server did not start")


@pytest.mark.asyncio
async def test_a_restarted_http_server_is_reconnected_once():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = _start_http_probe(port)
    client = MCPClient(
        servers=[
            {
                "name": "remote",
                "transport_type": "streamable_http",
                "url": f"http://127.0.0.1:{port}/mcp",
            }
        ]
    )
    try:
        await _connect(client)
        before = await _call(client, "remote", "echo", {"text": "before"})
        # The server restarts; it no longer knows this client's session.
        process.terminate()
        process.wait(timeout=10)
        process = _start_http_probe(port)

        after = await _call(client, "remote", "echo", {"text": "after"})
        info = dict(client.sessions["remote"])
    finally:
        await client.cleanup()
        process.terminate()
        process.wait(timeout=10)

    assert before["data"] == "before"
    assert after["status"] == "success", after
    assert after["data"] == "after"
    assert info["reconnects"] == 1


def test_only_a_gone_session_counts_as_dropped():
    from anyio import ClosedResourceError
    from mcp import MCPError

    from omnicoreagent.core.tools.mcp_tool_handler import is_dropped_session

    assert is_dropped_session(MCPError(-32000, "Connection closed"))
    assert is_dropped_session(MCPError(-32600, "Session terminated"))
    assert is_dropped_session(MCPError(-32600, "Session not found"))
    assert is_dropped_session(ClosedResourceError())
    # An ordinary bad request or a tool failure is not a reason to reconnect.
    assert not is_dropped_session(MCPError(-32600, "Invalid request: missing id"))
    assert not is_dropped_session(MCPError(-32602, "Invalid params"))
    assert not is_dropped_session(MCPError(-32001, "Request timed out"))
    assert not is_dropped_session(ValueError("boom"))
