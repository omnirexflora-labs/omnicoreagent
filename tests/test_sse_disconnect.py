"""Closing an SSE stream cancels its run, whatever the request timeout.

The 0.5.0rc4 gate: the run was cancelled only when the server closed the
stream's generator, which through the middleware did not reliably happen.
With the request timeout off, a long answer cut off after 2.5 s stayed
`running` for over five minutes, its heartbeat renewing, until the server
stopped.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.serve.sse import run_agent_stream
from test_execute_tool import _MODEL


class Streams:
    """An answer still streaming: text deltas, never the end."""

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        await asyncio.sleep(3600)

    async def llm_stream(self, messages, tools=None, **kwargs):
        while True:
            yield {"type": "text_delta", "text": "forests "}
            await asyncio.sleep(0.2)


class Hangs:
    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        await asyncio.sleep(3600)

    async def llm_stream(self, messages, tools=None, **kwargs):
        # A stream run (on_event) uses this: a long answer still coming.
        await asyncio.sleep(3600)
        yield {"type": "turn_complete"}


@pytest.mark.asyncio
async def test_a_closed_stream_cancels_its_run_with_no_request_timeout():
    agent = OmniCoreAgent(name="streamer", system_instruction="x", model_config=_MODEL,
                          agent_config={"guardrail_mode": "off", "enable_workspace_files": False})
    await agent.initialize()
    agent.llm_connection = Hangs()
    gone_at = time.monotonic() + 1.0

    async def is_disconnected():
        return time.monotonic() > gone_at

    started = time.monotonic()
    async for _ in run_agent_stream(agent, "write an essay", "cut", timeout_seconds=None,
                                    is_disconnected=is_disconnected):
        pass
    assert time.monotonic() - started < 10, "the stream kept going after the client left"

    for _ in range(50):
        runs = await agent.list_runs(session_id="cut")
        if runs and runs[0]["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert [r["status"] for r in runs] == ["cancelled"], [(r["status"], r.get("error")) for r in runs]
    await agent.cleanup()


_SERVER = '''
import asyncio, sys
sys.path.insert(0, {tests!r})
from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig
from test_sse_disconnect import Hangs, Streams

agent = OmniCoreAgent(name="streamer", system_instruction="x",
                      model_config={{"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}},
                      agent_config={{"guardrail_mode": "off", "enable_workspace_files": False}})
asyncio.run(agent.initialize())
agent.llm_connection = {model}()
OmniServe(agent, OmniServeConfig(host="127.0.0.1", port={port}, request_timeout=0,
          background_enabled=False)).start()
'''


@pytest.mark.parametrize("model", ["Hangs", "Streams"])
def test_a_client_hanging_up_on_a_real_server_cancels_the_run(tmp_path, model):
    # The 0.5.0rc5 gate: through a real server, Starlette cancels the stream
    # when the client goes, and the first await in its cleanup raised before
    # the run was cancelled: the run carried on to the end, every time.
    import json
    import socket
    import subprocess
    import sys
    import urllib.request
    from pathlib import Path

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    script = tmp_path / "server.py"
    script.write_text(_SERVER.format(tests=str(Path(__file__).parent), port=port, model=model))
    server = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(base + "/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        # Open the stream, read its first bytes, hang up.
        with socket.create_connection(("127.0.0.1", port)) as client:
            body = json.dumps({"query": "write an essay", "session_id": "cut"}).encode()
            client.sendall(b"POST /run HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                           + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            # Read as a client does, then hang up mid-stream.
            client.settimeout(0.5)
            until = time.monotonic() + 3
            while time.monotonic() < until:
                try:
                    client.recv(4096)
                except socket.timeout:
                    pass
        status = None
        for _ in range(40):
            time.sleep(0.5)
            runs = json.load(urllib.request.urlopen(base + "/runs?session_id=cut", timeout=5))["runs"]
            status = runs[0]["status"] if runs else None
            if status not in (None, "running"):
                break
        assert status == "cancelled", status
        # And no trace is left running: the request trace too (the 0.5.0rc6
        # gate: it stayed running forever after a hang-up).
        running = []
        for _ in range(20):
            time.sleep(0.5)
            answer = json.load(urllib.request.urlopen(base + "/telemetry/traces?status=running", timeout=5))
            running = answer.get("traces", answer) if isinstance(answer, dict) else answer
            if not running:
                break
        assert not running, running
    finally:
        server.kill()
        server.wait()
