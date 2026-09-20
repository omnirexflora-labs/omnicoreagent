"""A sandbox that dies mid-run is reported as lost, and the run goes on.

Found by the repository steward (production proving, P2): E2B reports a
killed sandbox as a *timeout* ("the connection to sandbox ... ended before
the stream completed", then "The sandbox was not found"), so a run whose
sandbox was taken away saw a command time out, then every later command
"time out" the same way, and never learned that the sandbox was gone. Now
the adapter tells a lost sandbox from a slow command, the run's scope drops
the session so the next command opens a fresh one, the model is told which
happened, and the trace records the session as closed because it was lost.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.telemetry import ActorType, InMemoryTelemetryStore, TelemetryActor, TelemetryRecorder
from omnicoreagent.core.telemetry.redaction import TelemetryConfig
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.governance import GovernanceEngine, build_default_policy
from omnicoreagent.sandbox import (
    LocalTestSandboxRuntime,
    SandboxCommandSpec,
    SandboxExecRequest,
    SandboxExecResult,
    SandboxExecutionService,
    SandboxManifest,
    build_sandbox_runtime,
)
from omnicoreagent.sandbox.models import SandboxAuthorityContext

AUTHORITY = SandboxAuthorityContext(authority_request_id="req", decision_id="d", policy_id="p")


# --- the E2B adapter ---------------------------------------------------------


class TimeoutException(Exception):
    """Named like E2B's: a dead sandbox and a slow command raise the same class."""


class FakeCommands:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.calls = []

    async def run(self, command, **kwargs):
        self.calls.append(command)
        if self.sandbox.alive:
            if self.sandbox.slow:
                raise TimeoutException("command timed out after 5s")
            return types.SimpleNamespace(exit_code=0, stdout="out", stderr="", error=None)
        raise TimeoutException(
            "the connection to sandbox e2b-1 ended before the stream completed: "
            "This error is likely due to sandbox timeout."
        )


class FakeFiles:
    async def make_dir(self, path):
        return True


class FakeSandbox:
    def __init__(self, **kwargs):
        self.sandbox_id = "e2b-1"
        self.alive = True
        self.slow = False
        self.commands = FakeCommands(self)
        self.files = FakeFiles()
        self.kill_calls = 0

    async def is_running(self, **kwargs):
        return self.alive

    async def kill(self, **kwargs):
        self.kill_calls += 1
        if not self.alive:
            raise TimeoutException("The sandbox was not found")
        self.alive = False
        return True


@pytest.fixture
def e2b(monkeypatch):
    made = {}

    async def create(**kwargs):
        made["sandbox"] = FakeSandbox(**kwargs)
        return made["sandbox"]

    module = types.ModuleType("e2b")
    module.AsyncSandbox = types.SimpleNamespace(create=create)
    monkeypatch.setitem(sys.modules, "e2b", module)
    return made


async def _e2b_session(e2b):
    runtime = build_sandbox_runtime({"provider": "e2b", "options": {"verify_network_isolation": False}})
    session = await runtime.create(SandboxManifest())
    return runtime, session, e2b["sandbox"]


@pytest.mark.asyncio
async def test_e2b_tells_a_lost_sandbox_from_a_slow_command(e2b):
    runtime, session, sandbox = await _e2b_session(e2b)
    request = SandboxExecRequest(command=["sleep", "60"], authority=AUTHORITY, timeout_seconds=5)

    sandbox.slow = True
    slow = await runtime.execute(session.session_id, request)
    assert (slow.timed_out, slow.exit_code) == (True, 124)
    assert not slow.metadata.get("session_terminated")

    sandbox.slow = False
    sandbox.alive = False
    lost = await runtime.execute(session.session_id, request)
    assert lost.timed_out is False
    assert lost.exit_code != 0
    assert lost.metadata["session_terminated"] is True and lost.metadata["session_lost"] is True
    assert "no longer exists" in lost.stderr and "e2b-1" in lost.stderr

    # Closing the record of a sandbox that is already gone is not an error.
    await runtime.terminate(session.session_id)


# --- the run's scope ----------------------------------------------------------


_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.calls.append(messages)
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn), finish_reason="tool_calls", usage=usage
        )


def _losing_runtime(seen: list) -> LocalTestSandboxRuntime:
    """The first command loses its sandbox; later ones run."""
    runtime = LocalTestSandboxRuntime()

    def sh(request, context):
        seen.append(context.session.session_id)
        if len(seen) == 1:
            return SandboxExecResult(
                exit_code=137,
                stderr="The sandbox no longer exists",
                metadata={"session_terminated": True, "session_lost": True},
            )
        return SandboxExecResult(exit_code=0, stdout="fresh")

    runtime.register_command("sh", sh)
    return runtime


@pytest.mark.asyncio
async def test_the_next_command_after_a_lost_sandbox_runs_in_a_fresh_one():
    seen: list = []
    model = ScriptedModel(
        [("c1", "execute", '{"command": "pytest -q"}')],
        [("c2", "execute", '{"command": "pytest -q"}')],
        "done",
    )
    runtime = _losing_runtime(seen)
    agent = OmniCoreAgent(
        name="builder",
        system_instruction="Use execute.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {
                "enabled": True,
                "profile": "permissive-dev",
                "sandbox_runtime": runtime,
                "allow_test_sandbox_runtime": True,
            },
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model

    result = await agent.run("test it", session_id="s1")

    assert result["response"] == "done"
    assert len(seen) == 2 and seen[0] != seen[1], "the second command opened a fresh session"
    assert runtime.sessions.keys() == set(), "both sessions are closed when the run ends"

    told = json.dumps(model.calls[1], default=lambda item: getattr(item, "__dict__", str(item)))
    assert "sandbox was lost" in told
    assert "fresh sandbox" in told

    trace = await agent.telemetry_store.get_trace(result["trace_id"])
    sessions = [
        (e.event_type, e.metadata.get("sandbox_session_id"), e.metadata.get("lost"))
        for e in trace.events
        if e.event_type in {"sandbox_session_created", "sandbox_session_closed"}
    ]
    assert sessions == [
        ("sandbox_session_created", seen[0], None),
        ("sandbox_session_closed", seen[0], True),
        ("sandbox_session_created", seen[1], None),
        ("sandbox_session_closed", seen[1], False),
    ]


# --- the provider's own id in the trace ----------------------------------------


@pytest.mark.asyncio
async def test_the_trace_names_the_provider_sandbox(e2b):
    """A person (or a chaos script) can find the sandbox a run used."""
    store = InMemoryTelemetryStore()
    recorder = TelemetryRecorder(store, TelemetryConfig(capture="full"))
    context = await recorder.start_trace(
        trace_id="trace-ref", run_id="run-ref", actor=TelemetryActor(type=ActorType.SYSTEM, name="t")
    )
    runtime = build_sandbox_runtime({"provider": "e2b", "options": {"verify_network_isolation": False}})
    engine = GovernanceEngine(
        build_default_policy("permissive-dev"), sandbox_runtime=runtime, telemetry_recorder=recorder
    )
    service = SandboxExecutionService(engine)

    session = await service.open_session()
    await service.execute(SandboxCommandSpec(command=["echo", "hi"]), session=session)
    await service.close_session(session)
    await recorder.end_trace()

    trace = await store.get_trace(context.trace_id)
    refs = {e.event_type: e.metadata.get("sandbox_ref") for e in trace.events if e.event_type.startswith("sandbox_")}
    assert refs == {
        "sandbox_session_created": "e2b-1",
        "sandbox_exec_started": "e2b-1",
        "sandbox_exec_completed": "e2b-1",
        "sandbox_session_closed": "e2b-1",
    }
