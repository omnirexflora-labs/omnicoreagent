"""MCP server settings are validated per transport before anything connects."""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.config import normalize_mcp_tool_config
from omnicoreagent.mcp_clients_connection.client import MCPClient

STDIO = {"name": "files", "transport_type": "stdio", "command": "npx", "args": ["-y", "server"]}
HTTP = {"name": "github", "transport_type": "streamable_http", "url": "https://mcp.example.com/mcp"}


def test_documented_examples_are_valid():
    examples = [
        STDIO,
        {**HTTP, "headers": {"Authorization": "Bearer token"}, "timeout": 60},
        {**HTTP, "auth": {"method": "oauth"}},
        {
            "name": "events",
            "transport_type": "sse",
            "url": "http://localhost:3000/sse",
            "headers": {"Authorization": "Bearer token"},
            "timeout": 60,
            "sse_read_timeout": 120,
        },
        {**STDIO, "cwd": "/srv", "env": {"TOKEN": "x"}, "connect_timeout": 10, "call_timeout": 5},
        {**HTTP, "auth": {"method": "oauth", "callback_port": 45123, "callback_timeout": 120}},
    ]
    for example in examples:
        normalize_mcp_tool_config(example)


def test_the_sdk_spelling_of_streamable_http_is_accepted():
    server = normalize_mcp_tool_config({**HTTP, "transport_type": "streamable-http"})
    assert server["transport_type"] == "streamable_http"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({**STDIO, "headers": {"A": "b"}}, "'headers' does not apply to stdio"),
        ({**STDIO, "url": "https://x"}, "'url' does not apply to stdio"),
        ({**STDIO, "auth": {"method": "oauth"}}, "'auth' does not apply to stdio"),
        ({**STDIO, "sse_read_timeout": 5}, "'sse_read_timeout' does not apply to stdio"),
        ({**HTTP, "command": "npx"}, "'command' does not apply to streamable_http"),
        ({**HTTP, "env": {"A": "b"}}, "'env' does not apply to streamable_http"),
        ({**HTTP, "cwd": "/srv"}, "'cwd' does not apply to streamable_http"),
        ({**HTTP, "args": ["x"]}, "'args' does not apply to streamable_http"),
    ],
)
def test_settings_that_do_nothing_for_the_transport_are_rejected(config, message):
    with pytest.raises(ValueError, match=message):
        normalize_mcp_tool_config(config)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({**STDIO, "comand": "npx"}, "Unknown MCP server setting 'comand'"),
        ({**HTTP, "url": "ftp://mcp.example.com"}, "url must start with http:// or https://"),
        ({"name": "files", "transport_type": "stdio"}, "command is required"),
        ({"name": "api", "transport_type": "sse"}, "url is required"),
        ({**STDIO, "transport_type": "websocket"}, "Unsupported MCP transport_type 'websocket'"),
        ({**STDIO, "args": "-y server"}, "args must be a list of strings"),
        ({**STDIO, "env": {"PORT": 8080}}, "env must map strings to strings"),
        ({**HTTP, "headers": ["Authorization"]}, "headers must map strings to strings"),
        ({**HTTP, "timeout": 0}, "timeout must be a positive number"),
        ({**STDIO, "connect_timeout": -1}, "connect_timeout must be a positive number"),
        ({**STDIO, "call_timeout": "fast"}, "call_timeout must be a positive number"),
        ({**HTTP, "auth": {"method": "basic"}}, "auth method must be 'oauth'"),
        ({**HTTP, "auth": {"method": "oauth", "scope": "x"}}, "Unknown auth setting 'scope'"),
        ({**HTTP, "auth": {"method": "oauth", "callback_port": 70000}}, "callback_port must be"),
        ({**HTTP, "auth": {"method": "oauth", "callback_timeout": 0}}, "callback_timeout must be"),
    ],
)
def test_invalid_settings_are_rejected_with_a_clear_message(config, message):
    with pytest.raises(ValueError, match=message):
        normalize_mcp_tool_config(config)


def test_errors_name_the_server():
    with pytest.raises(ValueError, match="'files'"):
        normalize_mcp_tool_config({**STDIO, "headers": {"A": "b"}})


def test_a_client_built_directly_validates_its_servers():
    with pytest.raises(ValueError, match="'headers' does not apply to stdio"):
        MCPClient(servers=[{**STDIO, "headers": {"A": "b"}}])


def test_http_timeouts_are_not_added_to_stdio_servers():
    server = normalize_mcp_tool_config(STDIO)
    assert "timeout" not in server
    assert "sse_read_timeout" not in server


def test_the_mcp_sdk_is_pinned_below_the_next_major_version():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    [mcp] = [dep for dep in pyproject["project"]["dependencies"] if dep.startswith("mcp")]
    assert mcp == "mcp[cli]>=2.2.0,<3"
