"""A port another process holds is found before the agent is loaded or started.

The 0.5.0rc2 gate (a stranger's app): uvicorn runs the app's startup before
it binds, and startup took 26-110 s on a loaded machine. A taken port showed
only at the end, and meanwhile a script polling that port reached another
tester's server and sent it a run.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from click.testing import CliRunner

from omnicoreagent import OmniServe, OmniServeConfig
from omnicoreagent.serve.cli import cli


@pytest.fixture
def taken_port():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    yield holder.getsockname()[1]
    holder.close()


def test_the_cli_refuses_a_taken_port_before_loading_the_agent(tmp_path, taken_port):
    agent_file = tmp_path / "agent.py"
    agent_file.write_text("raise SystemExit('the agent was loaded')\n")

    result = CliRunner().invoke(cli, ["run", "--agent", str(agent_file), "--host", "127.0.0.1",
                                      "--port", str(taken_port)])

    assert result.exit_code != 0
    assert f"127.0.0.1:{taken_port}" in result.output and "in use" in result.output
    assert "the agent was loaded" not in result.output


@pytest.mark.parametrize("how", ["start", "start_async"])
def test_the_server_binds_before_its_startup(taken_port, how):
    server = OmniServe.__new__(OmniServe)
    server.config = OmniServeConfig(host="127.0.0.1", port=taken_port)
    server.app = None  # never reached: the bind comes first

    with pytest.raises(OSError, match=f"127.0.0.1:{taken_port}.*in use"):
        if how == "start":
            server.start()
        else:
            asyncio.run(server.start_async())


def test_two_servers_cannot_both_take_the_port():
    # The 0.5.0rc3 gate: bound but not listening, a second server's bind
    # succeeded too; the first then crashed at the end of its startup.
    from omnicoreagent.serve.server import bind_server_socket

    first = bind_server_socket("127.0.0.1", 0)
    try:
        port = first.getsockname()[1]
        with pytest.raises(OSError, match="in use"):
            bind_server_socket("127.0.0.1", port).close()
    finally:
        first.close()


def test_the_cli_holds_the_port_while_the_agent_loads(tmp_path):
    # The port the CLI checked stays its own through the agent's loading: a
    # second server starting meanwhile is refused at once.
    import subprocess
    import sys
    import time

    from omnicoreagent.serve.server import bind_server_socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    agent_file = tmp_path / "agent.py"
    agent_file.write_text("import time\ntime.sleep(30)\nagent = None\n")
    server = subprocess.Popen(
        [sys.executable, "-c", "from omnicoreagent.serve.cli import main; main()",
         "run", "--agent", str(agent_file), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:  # until the CLI holds the port
            try:
                bind_server_socket("127.0.0.1", port).close()
            except OSError:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("the CLI never held the port while loading its agent")
    finally:
        server.kill()
        server.wait()
