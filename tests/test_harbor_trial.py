"""What the Harbor adapter decides, tested without a task container.

Harbor installs an agent into the task's own container and the task is solved by
running commands there. The adapter's judgement — what the agent file says, the
command that runs it, what a finished run reports, and the trajectory Harbor
reads — is in ``omnicoreagent.harbor.trial``, which imports nothing of Harbor's,
so all of it is tested here. The four things that cost a real trial to learn are
each held by a test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicoreagent.harbor.trial import (
    ATIF_SCHEMA_VERSION,
    DEFAULT_ENVIRONMENT_PASSTHROUGH,
    agent_file_source,
    atif_trajectory,
    build_wheel,
    host_runtime,
    install_source,
    run_command,
    usage_from_result,
)


def _source(**overrides) -> str:
    fields = {
        "task_dir": "/app",
        "workspace_dir": "/installed-agent/omnicoreagent/workspace",
        "model": "gpt-5.6-terra",
        "provider": "openai",
    }
    fields.update(overrides)
    return agent_file_source(**fields)


# --- the agent file -----------------------------------------------------------


def test_the_agent_file_is_valid_python_that_defines_an_agent():
    source = _source()

    compile(source, "trial_agent.py", "exec")
    assert "agent = OmniCoreAgent(" in source


def test_commands_run_on_the_host_in_the_tasks_own_directory():
    """The container is the boundary: an isolating sandbox is the wrong place."""
    source = _source(task_dir="/workspace/task")

    assert '"provider": "local"' in source
    assert '"working_dir": TASK_DIR' in source
    assert "TASK_DIR = '/workspace/task'" in source


def test_the_workspace_is_outside_the_task_directory():
    """Or it appears among the files the verifier checks."""
    source = _source(task_dir="/app", workspace_dir="/installed-agent/ws")

    assert "WORKSPACE_DIR = '/installed-agent/ws'" in source
    assert '"workspace_dir": WORKSPACE_DIR' in source


def test_host_commands_are_allowed_by_a_rule_of_their_own():
    """Every built-in profile denies or asks about them until one is added."""
    source = _source()

    assert 'capability="process.exec"' in source
    assert 'execution_surface="host"' in source
    assert "ask_process_exec" in source, "the asks that block commands are not dropped"


def test_the_environment_is_passed_through_by_name():
    """A task needs PYTHONPATH; it does not need the provider's key."""
    source = _source()

    assert "environment_passthrough" in source
    assert "PYTHONPATH" in source
    assert "inherit_environment" not in source


def test_the_key_is_read_from_the_environment_not_written_into_the_file():
    """Under the names Harbor used: they differ by provider."""
    source = _source(api_key_variables=("OPENAI_API_KEY", "OPENAI_TOKEN"))

    assert "os.environ.get(name)" in source
    assert "API_KEY_VARIABLES = ('OPENAI_API_KEY', 'OPENAI_TOKEN')" in source
    assert "LLM_API_KEY" in source, "the runtime's own variable is still tried"
    assert "sk-" not in source


def test_what_a_trial_can_change_reaches_the_file():
    source = _source(
        max_steps=12,
        command_timeout=45,
        capture="default",
        record_token_details=True,
    )

    assert "MAX_STEPS = 12" in source
    assert "COMMAND_TIMEOUT = 45" in source
    assert "CAPTURE = 'default'" in source
    assert '"record_token_details": True' in source


def _agent_namespace(source: str) -> dict:
    """What the agent file defines, without constructing the agent."""
    head = source.split("\nagent = OmniCoreAgent(")[0]
    namespace: dict = {}
    exec(compile(head, "trial_agent.py", "exec"), namespace)
    return namespace


def test_mcp_servers_the_trial_declared_reach_the_agent():
    """Harbor hands the agent the task's MCP servers; ignoring them would run
    the task without tools its author gave it, and say nothing."""
    servers = [
        {"name": "files", "transport_type": "stdio", "command": "mcp-files", "args": ["/data"]},
        {"name": "search", "transport_type": "streamable_http", "url": "http://search:8000/mcp"},
    ]
    source = _source(mcp_servers=servers)

    assert _agent_namespace(source)["MCP_SERVERS"] == servers
    assert "mcp_tools=MCP_SERVERS" in source


def test_a_declared_mcp_server_is_allowed_by_rules_of_its_own():
    """The profile asks before an MCP server starts or a tool of one is called,
    and nobody is there to answer: every call would be refused."""
    namespace = _agent_namespace(_source(mcp_servers=[
        {"name": "files", "transport_type": "stdio", "command": "mcp-files"}
    ]))

    policy = namespace["_policy"]()
    asks = {rule.rule_id for rule in policy.rules.ask}
    allows = {rule.rule_id: rule.capability for rule in policy.rules.allow}
    assert "ask_mcp_tool_call" not in asks
    assert "ask_mcp_server_start" not in asks
    assert allows["allow_trial_mcp_servers"] == "mcp.server.*"
    assert allows["allow_trial_mcp_tools"] == "tool.mcp.call"


def test_without_mcp_servers_nothing_about_mcp_is_allowed():
    policy = _agent_namespace(_source())["_policy"]()

    assert {rule.rule_id for rule in policy.rules.ask} >= {
        "ask_mcp_tool_call",
        "ask_mcp_server_start",
    }
    assert "allow_trial_mcp_tools" not in {rule.rule_id for rule in policy.rules.allow}


def test_the_deny_rules_survive_whatever_is_allowed():
    policy = _agent_namespace(_source(mcp_servers=[
        {"name": "files", "transport_type": "stdio", "command": "mcp-files"}
    ]))["_policy"]()

    assert {rule.rule_id for rule in policy.rules.deny} >= {
        "deny_credential_or_system_prompt_flow",
        "deny_raw_secret_read",
    }


def test_skills_are_read_from_where_harbor_put_them():
    source = _source(skills_dir="/harbor/skills")

    assert _agent_namespace(source)["SKILLS_DIR"] == "/harbor/skills"
    assert '"enable_agent_skills": bool(SKILLS_DIR)' in source
    assert '"skills_dir": SKILLS_DIR' in source


def test_without_skills_they_stay_off():
    assert _agent_namespace(_source())["SKILLS_DIR"] is None


def test_names_a_trial_adds_are_passed_through_to_commands():
    source = _source(passthrough=("PATH", "DATASET_ROOT"))

    assert _agent_namespace(source)["PASSTHROUGH"] == ("PATH", "DATASET_ROOT")


def test_the_whole_file_builds_an_agent_the_runtime_accepts(tmp_path, monkeypatch):
    """Everything above reads the file's constants; this runs all of it, so the
    runtime's own validation judges the MCP servers, the skills directory and
    the policy — which is what a container would do first."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    source = _source(
        task_dir=str(tmp_path),
        workspace_dir=str(tmp_path / "ws"),
        api_key_variables=("OPENAI_API_KEY",),
        mcp_servers=[
            {"name": "files", "transport_type": "stdio", "command": "mcp-files", "args": ["/data"]},
            {"name": "search", "transport_type": "streamable_http", "url": "http://search:8000/mcp"},
        ],
        skills_dir=str(tmp_path / "skills"),
    )
    namespace: dict = {}
    exec(compile(source, "trial_agent.py", "exec"), namespace)

    agent = namespace["agent"]
    assert [server["name"] for server in agent.mcp_tools] == ["files", "search"]
    assert agent.agent_config["skills_dir"] == str(tmp_path / "skills")
    assert agent.agent_config["enable_agent_skills"] is True


# --- what gets installed in the container ------------------------------------
#
# A harness must run the agent it was asked to run. The release on PyPI can be
# behind the runtime running the command — it had no ``cli`` module when this
# adapter was written, and a trial without an explicit install failed with
# ``No module named omnicoreagent.cli``.


def _no_build(root):
    raise AssertionError("nothing should be built")


def test_an_explicit_requirement_wins():
    assert install_source(
        version="1.2.3.dev4+abc", source_root=None, spec="omnicoreagent==1.0", build=_no_build
    ) == ("spec", "omnicoreagent==1.0")


def test_a_wheel_named_is_installed_as_it_is(tmp_path):
    wheel = tmp_path / "omnicoreagent-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    assert install_source(
        version="1.2.3.dev4", source_root=None, wheel=str(wheel), build=_no_build
    ) == ("wheel", str(wheel))


def test_a_wheel_that_is_not_there_is_an_error_not_a_fallback(tmp_path):
    with pytest.raises(ValueError, match="no such wheel"):
        install_source(
            version="1.2.3", source_root=None, wheel=str(tmp_path / "gone.whl"), build=_no_build
        )


@pytest.mark.parametrize("version", ["0.3.10", "1.0.0", "2.1.0.post1"])
def test_a_released_runtime_installs_the_same_release(version):
    assert install_source(version=version, source_root=None, build=_no_build) == (
        "spec",
        f"omnicoreagent=={version}",
    )


@pytest.mark.parametrize("version", ["0.3.10.dev538+e25b42b", "0.0.0+harbor", "1.0.0rc1+local"])
def test_a_development_runtime_is_built_and_shipped(tmp_path, version):
    """Its version names nothing PyPI has, so the container gets this build."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'omnicoreagent'\n")
    built = tmp_path / "dist" / "omnicoreagent-x-py3-none-any.whl"

    assert install_source(
        version=version, source_root=tmp_path, build=lambda root: str(built)
    ) == ("wheel", str(built))


def test_a_development_runtime_without_its_source_says_what_to_do():
    with pytest.raises(ValueError, match="install_spec"):
        install_source(version="0.3.10.dev538+e25b42b", source_root=None, build=_no_build)


def test_the_wheel_built_from_this_source_has_what_the_container_runs():
    """Built for real: the module the run command invokes and the adapter's own
    code must both be in it, or the container fails the way the release did."""
    import zipfile

    version, root = host_runtime()
    if root is None:
        pytest.skip("runs from an installed package, not a checkout")

    wheel = build_wheel(root, version=version)

    names = set(zipfile.ZipFile(wheel).namelist())
    assert "omnicoreagent/cli/__main__.py" in names
    assert "omnicoreagent/harbor/trial.py" in names
    assert build_wheel(root, version=version) == wheel, "built once, then reused"


# --- the command --------------------------------------------------------------


def test_the_command_runs_the_module_from_the_tasks_directory():
    """A console script is not always on PATH in a task container."""
    command = run_command(
        instruction="fix the failing test",
        agent_file="/installed-agent/trial_agent.py",
        output_dir="/logs/agent",
        task_dir="/app",
    )

    assert command.startswith("cd /app && ")
    assert "python3 -m omnicoreagent.cli run" in command
    assert "'fix the failing test'" in command
    assert "--approval-mode deny" in command
    assert "--budget-mode stop" in command


def test_the_command_carries_the_deadline_and_where_it_came_from():
    command = run_command(
        instruction="go",
        agent_file="/a.py",
        output_dir="/logs/agent",
        task_dir="/app",
        timeout_seconds=900.4,
        provenance={"adapter": "harbor", "trial_id": "t-1"},
        log_file="/logs/agent/omnicoreagent.txt",
    )

    assert "--timeout 900" in command
    assert "--provenance adapter=harbor" in command
    assert "--provenance trial_id=t-1" in command
    assert command.rstrip().endswith("| tee /logs/agent/omnicoreagent.txt")


def test_the_runtimes_own_interpreter_runs_it():
    """Not the task's Python: installing there could change a version the task
    depends on, and a modern Ubuntu refuses the install anyway (PEP 668)."""
    command = run_command(
        instruction="go",
        agent_file="/a.py",
        output_dir="/logs/agent",
        task_dir="/app",
        python="/installed-agent/omnicoreagent/venv/bin/python",
    )

    assert "/installed-agent/omnicoreagent/venv/bin/python -m omnicoreagent.cli" in command


def test_an_instruction_that_looks_like_shell_is_one_argument():
    command = run_command(
        instruction="rm -rf / ; echo $(whoami) `id`",
        agent_file="/a.py",
        output_dir="/logs/agent",
        task_dir="/app",
    )

    assert "'rm -rf / ; echo $(whoami) `id`'" in command


# --- what the run said --------------------------------------------------------


def test_what_a_finished_run_cost():
    usage = usage_from_result(
        {
            "status": "success",
            "exit_code": 0,
            "run_id": "run_1",
            "usage": {
                "request_tokens": 1200,
                "response_tokens": 340,
                "estimated_cost_usd": 0.0123,
                "details": {"cached_input_tokens": 800},
            },
        }
    )

    assert usage == {
        "n_input_tokens": 1200,
        "n_output_tokens": 340,
        "n_cache_tokens": 800,
        "cost_usd": 0.0123,
        "status": "success",
        "exit_code": 0,
        "termination_reason": None,
        "detail": None,
    }


@pytest.mark.parametrize("result", [None, {}, {"status": "timeout"}, {"usage": None}])
def test_a_run_that_said_little_still_reports_numbers(result):
    """A trial that timed out or was denied still has to report something."""
    usage = usage_from_result(result)

    assert usage["n_input_tokens"] == 0
    assert usage["n_output_tokens"] == 0
    assert usage["cost_usd"] is None


def test_the_cost_falls_back_to_what_the_trajectory_totalled():
    """``result.json`` reports tokens but no cost; the trajectory has it. A real
    trial reported ``cost_usd: None`` for a run that had cost 1.3 cents."""
    usage = usage_from_result(
        {"status": "success", "usage": {"request_tokens": 10, "response_tokens": 2}},
        trajectory=run_trajectory(),
    )

    assert usage["cost_usd"] == 0.0127302
    assert usage["n_input_tokens"] == 10, "what the run itself said still wins"


def test_a_run_without_a_result_takes_its_numbers_from_the_trajectory():
    usage = usage_from_result(None, trajectory=run_trajectory())

    assert usage["n_input_tokens"] == 13335
    assert usage["n_output_tokens"] == 576
    assert usage["n_cache_tokens"] == 11961
    assert usage["cost_usd"] == 0.0127302


# --- the trajectory Harbor reads ---------------------------------------------
#
# Every assertion below reads a *captured* run: tests/fixtures/harbor_run_trajectory.json
# is the trajectory of a real trial of this adapter (reward 1.0, three of its six
# steps kept, long strings shortened). The shape was guessed once, and the guess
# cost a trial: Harbor read a trajectory with zero steps because the command
# writes a *run* trajectory, whose per-trace trajectories sit under ``segments``.


def run_trajectory() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "harbor_run_trajectory.json").read_text()
    )


def _atif(trajectory: dict | None = None) -> dict:
    return atif_trajectory(
        trajectory if trajectory is not None else run_trajectory(),
        agent_name="omnicoreagent",
        agent_version="1.2.3",
        session_id="session_9",
        model_name="openai/gpt-5.6-terra",
    )


def test_the_steps_of_a_run_are_found_where_the_run_puts_them():
    """Under ``segments[*].trajectory.steps``, not at the top level."""
    document = _atif()

    assert document["schema_version"] == ATIF_SCHEMA_VERSION
    assert document["session_id"] == "session_9"
    assert document["trajectory_id"] == "run_2baa258c9eab4061ad09fc773fd0cb0e"
    # Sequential from 1, which Harbor's own model enforces.
    assert [step["step_id"] for step in document["steps"]] == [1, 2, 3, 4]
    assert [step["source"] for step in document["steps"]] == [
        "user",
        "agent",
        "agent",
        "agent",
    ]


def test_the_first_step_is_what_the_agent_was_asked():
    step = _atif()["steps"][0]

    assert "The tests in `/app` fail" in step["message"]
    assert "tool_calls" not in step


def test_an_agent_step_says_what_the_model_said_and_nothing_else():
    """A step whose model only called a tool has an empty message. Falling back
    to the last message *sent* would write the user's prompt into the agent's
    mouth, and a trainer reading this would learn it."""
    document = _atif()

    assert document["steps"][1]["message"] == ""
    assert document["steps"][3]["message"].startswith("Fixed `/app/receipts.py`")
    instruction = document["steps"][0]["message"]
    assert not any(step["message"] == instruction for step in document["steps"][1:])


def test_a_step_names_the_model_twice_over():
    """Once as the harness names it, because that is how Harbor keys the usage it
    computes from this file, and once as the provider named what answered — which
    the run records under ``provider_model``, not ``model``."""
    document = _atif()

    assert all(step.get("model_name") for step in document["steps"][1:])
    assert document["steps"][1]["model_name"] == "openai/gpt-5.6-terra"
    assert document["steps"][1]["metrics"]["extra"]["provider_model"] == "gpt-5.6-terra"
    facts = run_trajectory()["segments"][0]["trajectory"]["steps"][0]["model_calls"][0]
    assert "model" not in facts["facts"], "the key this once read"


def test_without_a_name_from_the_harness_the_providers_name_is_used():
    document = atif_trajectory(
        run_trajectory(), agent_name="omnicoreagent", agent_version="1.2.3"
    )

    assert document["steps"][1]["model_name"] == "gpt-5.6-terra"


def test_a_step_carries_its_tool_calls_and_what_they_answered():
    step = _atif()["steps"][2]

    assert step["tool_calls"] == [
        {
            "tool_call_id": "call_u5dLdKspQj1AqpjHdYT5SAGx",
            "function_name": "execute",
            "arguments": {"command": "cd /app && ls -la && pytest -q", "timeout": 300},
        }
    ]
    result = step["observation"]["results"][0]
    assert result["source_call_id"] == "call_u5dLdKspQj1AqpjHdYT5SAGx"
    assert "pytest: not found" in result["content"]


def test_an_observation_says_how_the_command_ran():
    """What a reviewer of an evaluation wants to know without opening our own
    trace: it failed, with which exit code, on which surface, under which rule."""
    result = _atif()["steps"][2]["observation"]["results"][0]

    assert result["extra"] == {
        "outcome": "error",
        "exit_code": 127,
        "sandbox_provider": "local",
        "matched_rule_ids": ["allow_host_commands"],
    }


def test_an_observation_of_a_tool_that_ran_no_command_claims_nothing():
    result = _atif()["steps"][1]["observation"]["results"][0]

    assert result["extra"] == {"outcome": "success"}


def test_a_step_carries_what_the_call_cost():
    metrics = _atif()["steps"][1]["metrics"]

    assert metrics["prompt_tokens"] == 1664
    assert metrics["completion_tokens"] == 24
    assert metrics["cached_tokens"] == 1598
    assert metrics["cost_usd"] == 0.0007711
    # And how the call went, which ATIF has no column of its own for.
    assert metrics["extra"]["finish_reason"] == "tool_calls"
    assert metrics["extra"]["reasoning_tokens"] == 8
    assert metrics["extra"]["cost_source"] == "provider_response"
    assert metrics["extra"]["latency_ms"] > 0


def test_the_logprobs_are_carried_when_the_run_recorded_them():
    """ATIF has a place for the tokens the model chose; a trainer wants them."""
    trajectory = run_trajectory()
    call = trajectory["segments"][0]["trajectory"]["steps"][0]["model_calls"][0]
    call["response"]["token_details"] = {
        "content": [{"token": "I", "logprob": -0.01}, {"token": " will", "logprob": -0.2}]
    }

    assert _atif(trajectory)["steps"][1]["metrics"]["logprobs"] == [-0.01, -0.2]


def test_the_totals_are_the_runs_own_and_include_the_cost():
    document = _atif()

    assert document["final_metrics"] == {
        "total_prompt_tokens": 13335,
        "total_completion_tokens": 576,
        "total_cached_tokens": 11961,
        "total_cost_usd": 0.0127302,
        "total_steps": 4,
    }


def test_what_a_reader_would_otherwise_have_to_guess_is_kept():
    document = _atif()

    extra = document["extra"]
    assert extra["run_id"] == "run_2baa258c9eab4061ad09fc773fd0cb0e"
    assert extra["status"] == "completed"
    # Including whether the recording was complete: this run's was not, because
    # a capture gap is not a reason to distrust the whole trajectory silently.
    assert extra["evidence_status"] == "partial"
    assert extra["trace_ids"] == ["trace_b5564e0c0d2944d79fa5a5e3ac5a815d"]
    assert document["agent"]["tool_definitions"], "a reader cannot judge a call alone"
    assert any(
        (tool.get("function") or {}).get("name") == "execute"
        for tool in document["agent"]["tool_definitions"]
    )


def test_a_trace_of_its_own_converts_too():
    """The archive stores one trace at a time; a caller may hand us that."""
    trace = run_trajectory()["segments"][0]["trajectory"]

    document = _atif(trace)

    assert [step["step_id"] for step in document["steps"]] == [1, 2, 3, 4]
    assert document["trajectory_id"] == "trace_b5564e0c0d2944d79fa5a5e3ac5a815d"
    assert document["final_metrics"]["total_cost_usd"] == 0.0127302


def test_steps_of_several_segments_are_numbered_as_one_trajectory():
    """A resumed run has a segment per attempt; ATIF requires one sequence."""
    trajectory = run_trajectory()
    trajectory["segments"] = trajectory["segments"] + [
        json.loads(json.dumps(trajectory["segments"][0]))
    ]

    document = _atif(trajectory)

    assert [step["step_id"] for step in document["steps"]] == [1, 2, 3, 4, 5, 6, 7]
    assert len(document["extra"]["trace_ids"]) == 2


def test_a_run_recorded_without_its_model_calls_still_becomes_a_trajectory():
    """The privacy-first capture records no prompts; the run still happened."""
    trajectory = run_trajectory()
    for step in trajectory["segments"][0]["trajectory"]["steps"]:
        step["model_calls"] = []

    document = _atif(trajectory)

    assert [step["source"] for step in document["steps"]] == ["agent"] * 3
    assert document["steps"][1]["tool_calls"][0]["function_name"] == "execute"
    assert "metrics" not in document["steps"][1]


def test_a_run_that_recorded_no_steps_is_still_a_valid_trajectory():
    """Harbor's model requires at least one step, so a run that ended before it
    took one says so in a step rather than producing a file nobody can read."""
    trajectory = run_trajectory()
    trajectory["segments"][0]["trajectory"]["steps"] = []

    document = _atif(trajectory)

    assert len(document["steps"]) == 1
    assert document["steps"][0]["source"] == "system"
    assert "completed" in document["steps"][0]["message"]


def test_tool_arguments_that_are_not_json_are_still_carried():
    trajectory = run_trajectory()
    step = trajectory["segments"][0]["trajectory"]["steps"][1]
    step["tool_calls"][0]["raw_arguments"] = "not json at all"

    document = _atif(trajectory)

    assert document["steps"][2]["tool_calls"][0]["arguments"] == {
        "raw": "not json at all"
    }


def test_the_trajectory_is_json_and_the_passthrough_list_is_named():
    document = _atif()

    assert json.loads(json.dumps(document)) == document
    assert "PYTHONPATH" in DEFAULT_ENVIRONMENT_PASSTHROUGH
    assert not any(
        name.endswith("API_KEY") for name in DEFAULT_ENVIRONMENT_PASSTHROUGH
    ), "a provider key must not be passed through to the model's commands"


# --- what went wrong around the run, where Harbor can see it -------------------
#
# A trial of the MCP task scored 1.0 with its MCP server dead: the server
# crashed on start, the run went on without the tools, and the agent read the
# answer out of the server's source instead. Our own trace recorded the failure;
# nothing Harbor reads said so.


def _with_mcp(status: str, error: str | None = None) -> dict:
    trajectory = run_trajectory()
    trajectory["segments"][0]["trajectory"]["harness"]["mcp_servers"] = [
        {
            "name": "rates",
            "transport_type": "stdio",
            "status": status,
            "server_info": None,
            "protocol_version": None,
            "tool_count": 0 if error else 1,
            "reconnects": 0,
            "error": error,
        }
    ]
    return trajectory


def test_a_failed_mcp_server_is_in_the_trajectory_harbor_reads():
    document = _atif(_with_mcp("failed", "Connection closed"))

    assert document["extra"]["mcp_servers"] == [
        {
            "name": "rates",
            "transport_type": "stdio",
            "status": "failed",
            "tool_count": 0,
            "error": "Connection closed",
        }
    ]


def test_a_connected_mcp_server_is_listed_too():
    document = _atif(_with_mcp("connected"))

    assert document["extra"]["mcp_servers"][0]["status"] == "connected"
    assert "error" not in document["extra"]["mcp_servers"][0]


def test_a_run_without_mcp_servers_says_nothing_about_them():
    assert "mcp_servers" not in _atif()["extra"]


def test_the_runs_security_warnings_are_carried_by_code():
    """That commands ran uncontained is a fact about the trial a reader should
    not have to find in our own trace."""
    assert _atif()["extra"]["security_warnings"] == ["host_execution_not_contained"]


def test_failed_mcp_servers_are_named_for_the_trials_record():
    from omnicoreagent.harbor.trial import failed_mcp_servers

    assert failed_mcp_servers(_with_mcp("failed", "Connection closed")) == [
        "rates: Connection closed"
    ]
    assert failed_mcp_servers(_with_mcp("connected")) == []
    # Every other state the MCP client reports leaves the run without the tools.
    for status in ("disconnected", "not_connected"):
        assert failed_mcp_servers(_with_mcp(status)) == [f"rates: {status}"]
    assert failed_mcp_servers(None) == []


# --- why a run ended ---------------------------------------------------------------


def test_why_a_run_ended_is_reported_not_only_its_status():
    """A step limit and a crash both have status "error"; the reason tells them apart."""
    usage = usage_from_result(
        {"status": "error", "exit_code": 1, "termination_reason": "max_steps",
         "response": "Agent reached its step limit.", "error": None}
    )

    assert usage["termination_reason"] == "max_steps"
    assert usage["detail"] == "Agent reached its step limit."


def test_a_successful_run_has_no_detail():
    usage = usage_from_result(
        {"status": "success", "exit_code": 0, "response": "Fixed it.", "termination_reason": None}
    )

    assert usage["detail"] is None


def test_the_detail_prefers_the_error_and_is_short():
    usage = usage_from_result(
        {"status": "timeout", "exit_code": 5, "error": "run exceeded its deadline",
         "response": "x" * 5000}
    )

    assert usage["detail"] == "run exceeded its deadline"
    long = usage_from_result({"status": "error", "response": "y" * 5000})
    assert len(long["detail"]) <= 300


# --- how the agent is asked to work -------------------------------------------------
#
# A real trial scored 0 with 66 of 83 checks passing: the agent stopped at step 13
# of 60 and generalized one passing check of invalid input into a claim about all
# of it. The full specification had reached it; the guidance was three lines.


def test_the_agent_is_told_to_read_everything_before_it_builds():
    instruction = _agent_namespace(_source())["SYSTEM_INSTRUCTION"]

    assert "read_artifact" in instruction, "an offloaded output must be read in full"
    assert "specification" in instruction


def test_the_agent_is_told_to_check_every_requirement_including_invalid_input():
    instruction = _agent_namespace(_source())["SYSTEM_INSTRUCTION"]

    assert "invalid input" in instruction
    assert "each requirement" in instruction


def test_the_agent_is_told_done_means_checked_and_to_report_honestly():
    instruction = _agent_namespace(_source())["SYSTEM_INSTRUCTION"]

    assert "while steps remain" in instruction
    assert "did not run" in instruction
    assert "Do not change the tests" in instruction


def test_the_final_answer_is_reviewed_by_default_and_can_be_turned_off():
    assert _agent_namespace(_source())["COMPLETION_REVIEW"] == 1
    assert _agent_namespace(_source(completion_review=0))["COMPLETION_REVIEW"] == 0
    assert '"completion_review": COMPLETION_REVIEW' in _source()
