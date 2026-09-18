"""OmniCoreAgent's MCP client against the official reference MCP server.

`@modelcontextprotocol/server-everything` is maintained by the MCP project
and built on the TypeScript SDK, so it checks our Python client against an
implementation we did not write. The script installs the pinned version into
a temporary directory with npm, runs it over stdio and streamable HTTP, and
checks each result the model would receive.

    PYTHONPATH=src .venv/bin/python engineering/validation/mcp_interop.py

Needs Node.js and npm, and network access for the first install.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.mcp_clients_connection.client import MCPClient

PACKAGE = "@modelcontextprotocol/server-everything"
VERSION = "2026.8.31"


def install() -> str:
    target = Path(tempfile.gettempdir()) / f"omnicoreagent-mcp-everything-{VERSION}"
    entry = target / "node_modules" / PACKAGE / "dist" / "index.js"
    if not entry.exists():
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["npm", "install", "--silent", "--prefix", str(target), f"{PACKAGE}@{VERSION}"],
            check=True,
        )
    return str(entry)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"server did not listen on {port}")


async def check(server: dict[str, Any]) -> list[str]:
    """Connect, call each probe, and return the failed expectations."""
    failures: list[str] = []
    client = MCPClient(servers=[server])
    await client.connect_to_servers()
    name = server["name"]
    try:
        info = client.sessions.get(name)
        if not info:
            return [f"{name}: did not connect (sessions: {list(client.sessions)})"]
        if info["server_info"]["name"] != "mcp-servers/everything":
            failures.append(f"{name}: reported identity {info['server_info']}")
        offered = {tool.name for tool in client.available_tools[name]}
        for tool in ("echo", "get-sum", "get-structured-content", "get-tiny-image"):
            if tool not in offered:
                failures.append(f"{name}: tool {tool} not offered")

        executor = ToolExecutor(MCPToolHandler(sessions=client.sessions, server_name=name))

        async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            return await executor.execute(tool, args)

        expectations = [
            (await call("echo", {"message": "hello"}), "success", lambda d: d == "Echo: hello"),
            (await call("get-sum", {"a": 2, "b": 5}), "success", lambda d: "7" in d),
            (
                await call("get-structured-content", {"location": "New York"}),
                "success",
                lambda d: isinstance(d, dict) and {"temperature", "conditions"} <= set(d),
            ),
            (
                await call("get-tiny-image", {}),
                "success",
                lambda d: any(
                    block.get("type") == "image" and block.get("mimeType") == "image/png"
                    for block in d["content"]
                ),
            ),
            (await call("get-sum", {"a": "x", "b": 1}), "error", lambda d: "-32602" in str(d)),
            (await call("no-such-tool", {}), "error", lambda d: "not found" in str(d)),
        ]
        for index, (result, status, data_ok) in enumerate(expectations, start=1):
            label = f"{name}: check {index} ({result['tool_name']})"
            if result["status"] != status:
                failures.append(f"{label}: status {result['status']}, expected {status}")
            elif not data_ok(result["data"]):
                failures.append(f"{label}: unexpected data {str(result['data'])[:200]}")
    finally:
        await client.cleanup()
    return failures


async def main() -> int:
    entry = install()
    failures = await check(
        {"name": "everything", "transport_type": "stdio", "command": "node", "args": [entry, "stdio"]}
    )
    port = _free_port()
    process = subprocess.Popen(
        ["node", entry, "streamableHttp"],
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        failures += await check(
            {
                "name": "everything_http",
                "transport_type": "streamable_http",
                "url": f"http://127.0.0.1:{port}/mcp",
            }
        )
    finally:
        process.terminate()
        process.wait(timeout=10)

    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print(f"{PACKAGE} {VERSION}: stdio and streamable HTTP, 6 checks each, all passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
