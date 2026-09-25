"""The runtime's own credentials never reach the model or any record.

A Harbor trial found this (finding 53): the model, looking for a "vault", ran
``tr '\\0' '\\n' </proc/222/environ`` and read the provider key out of the
runtime's own environment. Passing the environment to commands by name keeps a
key out of *their* environment, but a command running as the same user can read
the runtime's. The key then went into the model's context, the trace, and the
trajectory Harbor reads.

Nothing inside a container where the model has a root shell can hide a secret
that lives there; what the runtime can do is make sure its own credentials are
never handed to the model or written anywhere. These tests plant credentials,
make a real command print them, and look for them everywhere afterwards.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from omnicoreagent.core.credentials import (
    MARKER,
    looks_like_credential,
    register_credential,
    scrub_credentials,
)
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.harbor.trial import agent_file_source

MODEL_KEY = "sk-planted-model-key-5f1e2d3c4b5a69788796"
ENV_TOKEN = "ghp_plantedEnvToken0123456789abcdef"


# --- the scrubber ---------------------------------------------------------------


def test_a_registered_credential_is_replaced_wherever_it_appears():
    register_credential("sk-unit-test-key-000111222333")

    scrubbed = scrub_credentials(
        {
            "stdout": "OPENAI_API_KEY=sk-unit-test-key-000111222333\nok",
            "nested": ["x sk-unit-test-key-000111222333 y", 3, None],
        }
    )

    assert scrubbed == {
        "stdout": f"OPENAI_API_KEY={MARKER}\nok",
        "nested": [f"x {MARKER} y", 3, None],
    }


@pytest.mark.parametrize("value", ["k", "short", "true", "", "   "])
def test_a_value_too_short_to_be_a_credential_is_never_registered(value):
    """A test's api_key of "k" must not redact every k in every output."""
    register_credential(value)

    assert scrub_credentials("keep kinks and knots") == "keep kinks and knots"


@pytest.mark.parametrize(
    ("value", "credential"),
    [
        ("sk-proj-abcdefghijklmnop1234", True),
        ("ghp_0123456789abcdefghijABCDEFGHIJ", True),
        ("postgres://user:hunter2hunter2@db:5432/app", True),
        ("/home/user/.password-store", False),
        ("~/.config/tokens", False),
        ("false", False),
        ("1234567890123", False),
        ("a value with spaces in it", False),
    ],
)
def test_an_environment_value_is_taken_for_a_credential_only_when_it_looks_like_one(
    value, credential
):
    """PASSWORD_STORE_DIR is a path: scrubbing it would corrupt real output."""
    assert looks_like_credential(value) is credential


def test_names_that_merely_contain_auth_are_not_credentials():
    """GIT_AUTHOR_EMAIL is an address; scrubbing it corrupts `git log`."""
    from omnicoreagent.core.credentials import register_environment

    register_environment({"GIT_AUTHOR_EMAIL": "jane.doe@example.com"})

    assert scrub_credentials("Author: jane.doe@example.com") == "Author: jane.doe@example.com"


def test_an_authorization_header_is_a_credential_with_or_without_its_scheme():
    from omnicoreagent.core.credentials import register_config_credentials

    register_config_credentials(
        [{"name": "search", "headers": {"Authorization": "Bearer tok-abc123def456ghi789"}}]
    )

    assert scrub_credentials("sent tok-abc123def456ghi789") == f"sent {MARKER}"


def test_a_key_the_model_config_holds_is_a_credential():
    from omnicoreagent.core.credentials import register_config_credentials

    register_config_credentials({"provider": "openai", "model": "m", "api_key": "sk-config-held-0099887766"})

    assert scrub_credentials("key=sk-config-held-0099887766") == f"key={MARKER}"


# --- end to end, in this process ---------------------------------------------------


class RecordingModel:
    """A scripted model that keeps every request it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests: list = []

    def estimate_cost(self, usage):
        return None

    async def llm_stream(self, messages, tools=None, **kwargs):
        turn = await self.llm_call(messages, tools=tools)
        if turn.content:
            yield {"type": "text_delta", "text": turn.content}
        yield {"type": "turn_complete", "turn": turn}

    async def llm_call(self, messages, tools=None, **kwargs):
        self.requests.append(json.loads(json.dumps(messages, default=str)))
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn),
            finish_reason="tool_calls",
            usage=usage,
        )


def _trial_agent(tmp_path: Path) -> dict:
    """The agent a Harbor trial runs: host commands in the task's directory."""
    source = agent_file_source(
        task_dir=str(tmp_path / "task"),
        workspace_dir=str(tmp_path / "workspace"),
        model="gpt-5.6-terra",
        provider="openai",
        api_key_variables=("OPENAI_API_KEY",),
    )
    namespace: dict = {}
    exec(compile(source, "trial_agent.py", "exec"), namespace)
    return namespace


@pytest.mark.skipif(sys.platform == "win32", reason="the local provider's shell is POSIX")
@pytest.mark.asyncio
async def test_a_command_that_prints_the_runtimes_credentials_hands_them_to_nobody(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", MODEL_KEY)
    monkeypatch.setenv("GITHUB_TOKEN", ENV_TOKEN)
    task = tmp_path / "task"
    task.mkdir()
    # A task repository with a .env in it is ordinary.
    (task / "secrets.env").write_text(f"OPENAI_API_KEY={MODEL_KEY}\nGITHUB_TOKEN={ENV_TOKEN}\n")
    monkeypatch.chdir(task)
    agent = _trial_agent(tmp_path)["agent"]
    await agent.initialize()
    model = RecordingModel(
        [("c1", "execute", json.dumps({"command": "cat secrets.env"}))],
        "done",
    )
    agent.llm_connection = model
    events: list = []

    try:
        result = await agent.run("look", session_id="leak", on_event=events.append)
        trajectory = await agent.get_trajectory(result["trace_id"])
    finally:
        await agent.cleanup()

    seen_by_model = json.dumps(model.requests)
    assert MARKER in seen_by_model, "the command's output reached the model, scrubbed"
    everything = {
        "what the model was sent": seen_by_model,
        "the run's result": json.dumps(result, default=str),
        "the trajectory": json.dumps(trajectory, default=str),
        "the stream": json.dumps([getattr(e, "__dict__", e) for e in events], default=str),
        "the workspace": "\n".join(
            path.read_text(errors="replace")
            for path in (tmp_path / "workspace").rglob("*")
            if path.is_file()
        ),
    }
    for where, text in everything.items():
        assert MODEL_KEY not in text, f"the model key is in {where}"
        assert ENV_TOKEN not in text, f"the environment token is in {where}"


# --- end to end, the way the trial read it: from /proc ---------------------------

_CHILD = textwrap.dedent(
    """
    import asyncio, json, sys
    sys.path.insert(0, {tests!r})
    from test_credential_scrubbing import RecordingModel, _trial_agent
    from pathlib import Path

    async def main():
        tmp = Path({tmp!r})
        (tmp / "task").mkdir()
        agent = _trial_agent(tmp)["agent"]
        await agent.initialize()
        # Exactly what the trial's model ran: the runtime's environment, read
        # from /proc by a command running as the same user.
        model = RecordingModel(
            [("c1", "execute", json.dumps({{"command": "tr '\\\\0' '\\\\n' </proc/$PPID/environ | grep -i key"}}))],
            "done",
        )
        agent.llm_connection = model
        result = await agent.run("look", session_id="proc")
        trajectory = await agent.get_trajectory(result["trace_id"])
        await agent.cleanup()
        print(json.dumps({{"model": model.requests, "trajectory": trajectory}}, default=str))

    asyncio.run(main())
    """
)


@pytest.mark.skipif(not Path("/proc/self/environ").exists(), reason="needs /proc")
def test_the_runtimes_environment_read_from_proc_reaches_nobody(tmp_path):
    import os

    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "PYTHONPATH", "VIRTUAL_ENV", "TMPDIR"}
    }
    # In the process's environment from its start, as Harbor puts it there.
    env["OPENAI_API_KEY"] = MODEL_KEY
    script = _CHILD.format(tests=str(Path(__file__).parent), tmp=str(tmp_path))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    output = completed.stdout
    assert MODEL_KEY not in output
    assert MODEL_KEY not in completed.stderr
    record = json.loads(output.strip().splitlines()[-1])
    seen = json.dumps(record["model"])
    # The command really did read the key; the model got the marker instead.
    assert f"OPENAI_API_KEY={MARKER}" in seen
