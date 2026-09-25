"""The adapter as Harbor sees it, checked against Harbor itself.

``tests/test_harbor_trial.py`` holds what the adapter decides, without Harbor.
This holds the part only Harbor can judge: that the class satisfies its
installed-agent contract, that the options it offers are real flags, and — the
one that matters most — that the trajectory we write **validates against
Harbor's own model**, so a trial of this agent can be read beside a trial of
any other.

Installed with the ``harbor`` extra, which CI has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

harbor = pytest.importorskip("harbor", reason="needs the harbor extra")

from harbor.agents.installed.base import BaseInstalledAgent  # noqa: E402
from harbor.models.trajectories.trajectory import Trajectory  # noqa: E402

from omnicoreagent.harbor import atif_trajectory  # noqa: E402
from omnicoreagent.harbor.agent import (  # noqa: E402
    AGENT_FILE,
    NATIVE_SUBDIR,
    OUTPUT_DIR,
    WORKSPACE_DIR,
    OmniCoreAgentHarbor,
)
from test_harbor_trial import run_trajectory  # noqa: E402


def test_the_adapter_satisfies_harbors_installed_agent_contract():
    assert issubclass(OmniCoreAgentHarbor, BaseInstalledAgent)
    assert OmniCoreAgentHarbor.name() == "omnicoreagent"
    # Every abstract method Harbor declares is implemented.
    assert not getattr(OmniCoreAgentHarbor, "__abstractmethods__", frozenset())
    # And Harbor can find it by the path we document.
    assert (
        OmniCoreAgentHarbor.import_path()
        == "omnicoreagent.harbor.agent:OmniCoreAgentHarbor"
    )


def test_it_declares_the_trajectory_format_it_writes():
    assert OmniCoreAgentHarbor.capabilities.atif is True
    # What it does not claim: a first adapter runs a task and reports honestly.
    assert OmniCoreAgentHarbor.capabilities.resume is False
    assert OmniCoreAgentHarbor.capabilities.handoff is False


def test_the_options_a_trial_can_set_are_harbor_flags():
    fields = OmniCoreAgentHarbor.options_model.model_fields

    for name in (
        "max_steps",
        "command_timeout",
        "approval_mode",
        "budget_mode",
        "capture",
        "record_token_details",
        "install_spec",
    ):
        assert name in fields, f"{name} is not offered to a trial"
        assert fields[name].description, f"{name} has no description"
    assert "run_timeout" in fields
    # Harbor does not tell an installed agent its timeout, so ours is explicit
    # and unset by default rather than guessed.
    assert fields["run_timeout"].default is None
    # The defaults a trial should have: nobody is there to answer.
    assert fields["approval_mode"].default == "deny"
    assert fields["budget_mode"].default == "stop"
    # And the capture a reviewer or a trainer needs.
    assert fields["capture"].default == "full"


def test_the_agents_own_files_live_outside_the_task():
    """A verifier reads the task's directory; the agent's files are not its work."""
    assert OUTPUT_DIR.endswith(f"/{NATIVE_SUBDIR}"), (
        "the run's output would land on the trajectory Harbor reads"
    )
    for path in (AGENT_FILE, WORKSPACE_DIR, OUTPUT_DIR):
        assert path.startswith("/installed-agent") or path.startswith("/logs"), path




def test_the_trajectory_we_write_is_valid_atif():
    """Harbor's own model parses a real run's trajectory: this is what makes the
    trial comparable with a trial of any other agent."""
    document = atif_trajectory(
        run_trajectory(),
        agent_name="omnicoreagent",
        agent_version="1.2.3",
        session_id="session_9",
        model_name="openai/gpt-5.6-terra",
    )

    trajectory = Trajectory.model_validate(document)

    assert trajectory.schema_version == document["schema_version"]
    assert trajectory.agent.name == "omnicoreagent"
    assert [step.step_id for step in trajectory.steps] == [1, 2, 3, 4]
    assert trajectory.steps[0].source == "user"
    command = trajectory.steps[2]
    assert command.tool_calls and command.tool_calls[0].function_name == "execute"
    assert command.observation
    assert "pytest: not found" in command.observation.results[0].content
    assert command.metrics and command.metrics.prompt_tokens == 1738
    # The numbers Harbor reports for the trial.
    assert trajectory.final_metrics.total_cost_usd == 0.0127302
    assert trajectory.final_metrics.total_steps == 4


def test_harbor_reads_the_model_usage_out_of_what_we_wrote():
    """Harbor derives its per-model usage from the ATIF file itself, so a model
    name in the wrong place there is a trial reported with no usage at all."""
    from harbor.utils.trajectory_utils import compute_model_usage

    document = atif_trajectory(
        run_trajectory(),
        agent_name="omnicoreagent",
        agent_version="1.2.3",
        model_name="openai/gpt-5.6-terra",
    )

    usage = compute_model_usage(Trajectory.model_validate(document))

    assert usage, "Harbor found no model usage in our trajectory"
    # Keyed the way the harness names the model, so it reads beside its own
    # results rather than under a spelling only the provider uses.
    entry = usage["openai/gpt-5.6-terra"]
    assert entry.n_input_tokens == 1664 + 1738 + 2957
    assert entry.n_output_tokens == 24 + 45 + 65
    assert entry.n_cache_tokens == 1598 + 1661 + 2633
    assert entry.cost_usd == pytest.approx(0.0007711 + 0.0010632 + 0.0021151)


def test_a_trajectory_without_model_calls_is_still_valid_atif():
    trajectory = run_trajectory()
    for step in trajectory["segments"][0]["trajectory"]["steps"]:
        step["model_calls"] = []

    document = atif_trajectory(
        trajectory, agent_name="omnicoreagent", agent_version="1.2.3"
    )

    parsed = Trajectory.model_validate(document)
    assert len(parsed.steps) == 3


def test_a_run_that_took_no_step_is_still_valid_atif():
    """Harbor's model requires a step; a run that died before taking one must
    not produce a file its reader rejects."""
    trajectory = run_trajectory()
    trajectory["segments"] = []
    trajectory["steps"] = []

    document = atif_trajectory(
        trajectory, agent_name="omnicoreagent", agent_version="1.2.3"
    )

    assert len(Trajectory.model_validate(document).steps) == 1


def test_an_option_is_read_from_where_harbor_puts_it(tmp_path: Path):
    """Harbor parses the options model onto ``options``; a default holds when
    a trial set nothing. Guessing that attribute cost a real trial."""
    plain = OmniCoreAgentHarbor(model_name="openai/gpt-5.6-terra", logs_dir=tmp_path)
    assert plain._option("approval_mode", "deny") == "deny"
    assert plain._option("max_steps", 60) == 60

    # Harbor passes a trial's flags as kwargs and builds the options itself.
    chosen = OmniCoreAgentHarbor(
        model_name="openai/gpt-5.6-terra",
        logs_dir=tmp_path,
        max_steps=7,
        approval_mode="allow",
    )
    assert chosen._option("max_steps", 60) == 7
    assert chosen._option("approval_mode", "allow") == "allow"

    # And an option this agent does not offer is refused, not ignored.
    with pytest.raises(ValueError, match="Unknown option"):
        OmniCoreAgentHarbor(
            model_name="openai/gpt-5.6-terra", logs_dir=tmp_path, no_such_option=1
        )


@pytest.mark.asyncio
async def test_a_finished_run_is_reported_to_harbor(tmp_path: Path):
    """What Harbor reads after the trial: tokens, cost, and the ATIF file."""
    from harbor.models.agent.context import AgentContext

    # Harbor hands the agent its own directory and reads trajectory.json there;
    # the run's own output, which has a trajectory of that name too, is under a
    # subdirectory of it.
    logs = tmp_path / "agent"
    (logs / NATIVE_SUBDIR).mkdir(parents=True)
    (logs / NATIVE_SUBDIR / "result.json").write_text(
        json.dumps(
            {
                "status": "success",
                "exit_code": 0,
                "run_id": "run_9",
                "session_id": "session_9",
                # As a real run writes it: the tokens, and no cost.
                "usage": {
                    "requests": 6,
                    "request_tokens": 13335,
                    "response_tokens": 576,
                    "total_tokens": 13911,
                },
            }
        )
    )
    (logs / NATIVE_SUBDIR / "trajectory.json").write_text(json.dumps(run_trajectory()))

    agent = OmniCoreAgentHarbor(
        model_name="openai/gpt-5.6-terra", logs_dir=logs, version="1.2.3"
    )
    context = AgentContext()
    agent.populate_context_post_run(context)

    assert context.n_input_tokens == 13335
    assert context.n_output_tokens == 576
    # Both of these the run reported only in its trajectory.
    assert context.n_cache_tokens == 11961
    assert context.cost_usd == 0.0127302
    assert (context.metadata or {})["omnicoreagent_run_id"] == "run_9"
    # And the trajectory is where Harbor looks for it, in its own format,
    # beside — not over — the run's own.
    written = json.loads((logs / "trajectory.json").read_text())
    parsed = Trajectory.model_validate(written)
    assert parsed.agent.name == "omnicoreagent"
    assert len(parsed.steps) == 4, "a trajectory with no steps tells Harbor nothing"
    ours = json.loads((logs / NATIVE_SUBDIR / "trajectory.json").read_text())
    assert ours["segments"], "our own was overwritten"


@pytest.mark.asyncio
async def test_a_run_that_wrote_nothing_does_not_break_the_trial(tmp_path: Path):
    from harbor.models.agent.context import AgentContext

    logs = tmp_path / "logs"
    logs.mkdir()
    agent = OmniCoreAgentHarbor(model_name="openai/gpt-5.6-terra", logs_dir=logs)
    context = AgentContext()

    agent.populate_context_post_run(context)

    assert context.n_input_tokens == 0
    assert not (logs / "trajectory.json").exists()


# --- nothing a trial passes is silently dropped --------------------------------


class FakeEnvironment:
    """What an installed agent touches of a task container, recorded."""

    default_user = None

    def __init__(self, task_dir: str = "/app"):
        self.task_dir = task_dir
        self.commands: list[str] = []
        self.uploads: dict[str, bytes] = {}

    async def exec(self, command, user=None, env=None, cwd=None, timeout_sec=None):
        from harbor.environments.base import ExecResult

        self.commands.append(command)
        stdout = f"{self.task_dir}\n" if command.strip().endswith("pwd") else ""
        return ExecResult(return_code=0, stdout=stdout, stderr="")

    async def upload_file(self, source_path, target_path):
        self.uploads[str(target_path)] = Path(source_path).read_bytes()


def _agent(tmp_path: Path, **kwargs) -> OmniCoreAgentHarbor:
    return OmniCoreAgentHarbor(
        model_name="openai/gpt-5.6-terra", logs_dir=tmp_path / "agent", **kwargs
    )


def _agent_file(environment: FakeEnvironment) -> dict:
    source = environment.uploads[AGENT_FILE].decode()
    head = source.split("\nagent = OmniCoreAgent(")[0]
    namespace: dict = {}
    exec(compile(head, "trial_agent.py", "exec"), namespace)
    return namespace


@pytest.mark.asyncio
async def test_the_trials_mcp_servers_reach_the_agent_file(tmp_path: Path):
    from harbor.models.task.config import MCPServerConfig

    agent = _agent(
        tmp_path,
        install_spec="omnicoreagent==1.0",
        mcp_servers=[
            MCPServerConfig(name="files", transport="stdio", command="mcp-files", args=["/data"]),
            MCPServerConfig(name="search", transport="http", url="http://search:8000/mcp"),
            MCPServerConfig(name="events", transport="sse", url="http://events:9000/sse"),
        ],
    )
    environment = FakeEnvironment()

    await agent.setup(environment)

    assert _agent_file(environment)["MCP_SERVERS"] == [
        {"name": "files", "transport_type": "stdio", "command": "mcp-files", "args": ["/data"]},
        {"name": "search", "transport_type": "streamable_http", "url": "http://search:8000/mcp"},
        {"name": "events", "transport_type": "sse", "url": "http://events:9000/sse"},
    ]


@pytest.mark.asyncio
async def test_the_trials_skills_reach_the_agent_file(tmp_path: Path):
    agent = _agent(tmp_path, install_spec="omnicoreagent==1.0", skills_dir="/harbor/skills")
    environment = FakeEnvironment()

    await agent.setup(environment)

    assert _agent_file(environment)["SKILLS_DIR"] == "/harbor/skills"


@pytest.mark.asyncio
async def test_names_set_with_agent_env_reach_the_commands_but_secrets_do_not(tmp_path: Path):
    """``--ae`` sets the agent's environment. A name a task needs is passed on to
    the model's commands; a credential stays with the runtime."""
    agent = _agent(
        tmp_path,
        install_spec="omnicoreagent==1.0",
        extra_env={"DATASET_ROOT": "/data", "SEARCH_API_KEY": "not-for-commands"},
    )
    environment = FakeEnvironment()

    await agent.setup(environment)

    passthrough = _agent_file(environment)["PASSTHROUGH"]
    assert "DATASET_ROOT" in passthrough
    assert "SEARCH_API_KEY" not in passthrough
    assert "PYTHONPATH" in passthrough, "the defaults are kept"


@pytest.mark.asyncio
async def test_by_default_the_container_gets_this_very_runtime(tmp_path: Path, monkeypatch):
    """Not whatever PyPI has: a development build is built and uploaded."""
    import omnicoreagent.harbor.agent as adapter

    wheel = tmp_path / "omnicoreagent-9.9.9.dev1+abc-py3-none-any.whl"
    wheel.write_bytes(b"a wheel")
    monkeypatch.setattr(adapter, "host_runtime", lambda: ("9.9.9.dev1+abc", tmp_path))
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'omnicoreagent'\n")
    monkeypatch.setattr(adapter, "build_wheel", lambda root, version=None: str(wheel))
    agent = _agent(tmp_path)
    environment = FakeEnvironment()

    await agent.setup(environment)

    remote = f"{adapter.AGENT_DIR}/dist/{wheel.name}"
    assert environment.uploads[remote] == b"a wheel"
    installs = [command for command in environment.commands if "pip install" in command]
    assert any(f"pip install --quiet {remote}" in command for command in installs), installs
    assert not any("omnicoreagent==" in command for command in installs)


@pytest.mark.asyncio
async def test_a_released_runtime_installs_its_own_release(tmp_path: Path, monkeypatch):
    import omnicoreagent.harbor.agent as adapter

    monkeypatch.setattr(adapter, "host_runtime", lambda: ("0.3.10", None))
    agent = _agent(tmp_path)
    environment = FakeEnvironment()

    await agent.setup(environment)

    assert any("omnicoreagent==0.3.10" in command for command in environment.commands)


@pytest.mark.asyncio
async def test_a_development_runtime_without_source_fails_the_install_with_a_sentence(
    tmp_path: Path, monkeypatch
):
    import omnicoreagent.harbor.agent as adapter

    monkeypatch.setattr(adapter, "host_runtime", lambda: ("0.3.10.dev5+abc", None))
    agent = _agent(tmp_path)

    with pytest.raises(RuntimeError, match="install_spec"):
        await agent.setup(FakeEnvironment())


def test_resume_and_loading_a_trajectory_are_not_claimed():
    """Harbor refuses ``--resume-trajectory`` and ``--load-trajectory`` for an
    agent that does not declare them, with a sentence naming the agent."""
    capabilities = OmniCoreAgentHarbor.capabilities
    assert capabilities.resume is False
    assert capabilities.load_atif_trajectory is False
    assert capabilities.load_native_trajectory is False


@pytest.mark.asyncio
async def test_a_failed_mcp_server_is_in_the_trials_record(tmp_path: Path, caplog):
    """A trial can score 1.0 with its MCP server dead; its record must say so."""
    import logging

    from harbor.models.agent.context import AgentContext

    logs = tmp_path / "agent"
    (logs / NATIVE_SUBDIR).mkdir(parents=True)
    trajectory = run_trajectory()
    trajectory["segments"][0]["trajectory"]["harness"]["mcp_servers"] = [
        {"name": "rates", "transport_type": "stdio", "status": "failed",
         "tool_count": 0, "error": "Connection closed"}
    ]
    (logs / NATIVE_SUBDIR / "trajectory.json").write_text(json.dumps(trajectory))
    (logs / NATIVE_SUBDIR / "result.json").write_text(json.dumps({"status": "success"}))
    agent = OmniCoreAgentHarbor(model_name="openai/gpt-5.6-terra", logs_dir=logs)
    context = AgentContext()

    with caplog.at_level(logging.WARNING):
        agent.populate_context_post_run(context)

    assert context.metadata["omnicoreagent_mcp_failed"] == ["rates: Connection closed"]
    assert "rates: Connection closed" in caplog.text
