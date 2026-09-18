"""OmniCoreAgent's MCP client against a real MCP 2 server on every transport.

The probe server's tools report what they received (headers, working
directory, environment), so each test proves the configuration reached the
server rather than only that a call was made.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.mcp_clients_connection.client import MCPClient
from omnicoreagent.mcp_clients_connection.transports import stdio_server_parameters

PROBE = str(Path(__file__).parent / "fixtures" / "mcp_probe_server.py")
PROBE_TOOLS = {
    "forecast",
    "echo",
    "fail",
    "request_header",
    "working_directory",
    "environment",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"probe server exited with {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"probe server did not listen on {port}")


@pytest.fixture(scope="module", params=["streamable-http", "sse"])
def http_probe(request):
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, PROBE, request.param, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port, process)
        path = "/mcp" if request.param == "streamable-http" else "/sse"
        transport = "streamable_http" if request.param == "streamable-http" else "sse"
        yield transport, f"http://127.0.0.1:{port}{path}"
    finally:
        process.terminate()
        process.wait(timeout=10)


async def _call(client: MCPClient, server: str, tool: str, args: dict) -> dict:
    name = client.state.resolve_server_name(server)
    handler = MCPToolHandler(sessions=client.sessions, server_name=name)
    return await ToolExecutor(handler).execute(tool, args)


async def _connected(server: dict) -> MCPClient:
    client = MCPClient(servers=[server])
    await client.connect_to_servers()
    name = client.state.resolve_server_name(server["name"])
    assert client.sessions[name]["connected"] is True
    assert {tool.name for tool in client.available_tools[name]} == PROBE_TOOLS
    return client


@pytest.mark.asyncio
async def test_stdio_server_connects_lists_tools_and_answers(tmp_path):
    client = await _connected(
        {
            "name": "probe",
            "transport_type": "stdio",
            "command": sys.executable,
            "args": [PROBE, "stdio"],
            "cwd": str(tmp_path),
            "env": {"PROBE_MARKER": "from-config"},
        }
    )
    try:
        forecast = await _call(client, "probe", "forecast", {"city": "Lagos"})
        cwd = await _call(client, "probe", "working_directory", {})
        env = await _call(client, "probe", "environment", {"name": "PROBE_MARKER"})
        failed = await _call(client, "probe", "fail", {"reason": "boom"})
    finally:
        await client.cleanup()

    assert forecast["status"] == "success"
    assert forecast["data"] == {"city": "Lagos", "temp": 31}
    assert Path(cwd["data"]).resolve() == tmp_path.resolve()
    assert env["data"] == "from-config"
    assert failed["status"] == "error"


@pytest.mark.asyncio
async def test_http_servers_connect_and_receive_configured_headers(http_probe):
    transport, url = http_probe
    client = await _connected(
        {
            "name": "remote",
            "transport_type": transport,
            "url": url,
            "headers": {"X-Probe": "configured"},
            "timeout": 30,
            "sse_read_timeout": 120,
        }
    )
    try:
        header = await _call(client, "remote", "request_header", {"name": "x-probe"})
        echo = await _call(client, "remote", "echo", {"text": "hi"})
    finally:
        await client.cleanup()

    assert header["data"] == "configured"
    assert echo["data"] == "hi"


def test_stdio_parameters_default_args_and_keep_cwd():
    params = stdio_server_parameters({"name": "probe", "command": "probe-server"})
    assert params.args == []
    assert params.cwd is None

    params = stdio_server_parameters(
        {"name": "probe", "command": "probe-server", "args": ["--x"], "cwd": "/srv"}
    )
    assert (params.args, str(params.cwd)) == (["--x"], "/srv")


@pytest.mark.asyncio
async def test_session_uses_float_timeout_and_identifies_the_client(monkeypatch):
    from omnicoreagent.mcp_clients_connection import client as client_module

    seen = {}
    original = client_module.ClientSession

    class Recording(original):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(client_module, "ClientSession", Recording)
    client = await _connected(
        {
            "name": "probe",
            "transport_type": "stdio",
            "command": sys.executable,
            "args": [PROBE, "stdio"],
        }
    )
    await client.cleanup()

    assert isinstance(seen["read_timeout_seconds"], float)
    assert seen["client_info"].name == "omnicoreagent"
    assert seen["client_info"].version


def test_agent_mcp_config_keeps_the_working_directory():
    from omnicoreagent.core.runtime.config import normalize_mcp_tools

    [server] = normalize_mcp_tools(
        [{"name": "probe", "command": "probe-server", "cwd": "/srv/probe"}]
    )

    assert server["cwd"] == "/srv/probe"
