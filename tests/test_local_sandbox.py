"""The local sandbox: commands run as processes on this machine.

It is not isolated, and says so: commands are authorized as host execution,
a policy that requires a sandbox refuses them, the built-in profiles deny or
ask until a rule allows them, and a manifest asking for isolation the backend
cannot give is refused. What it does enforce — time limits, bounded output,
file access through the runtime inside the working directory, the host
environment kept out by default — is tested against real processes.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
import pytest_asyncio

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.governance import (
    GovernanceEngine,
    PolicyRuleConditions,
    PolicyConstraints,
    PolicyEffect,
    PolicyEnvelope,
    PolicyMode,
    PolicyRule,
    PolicyRuleSet,
    build_default_policy,
)
from omnicoreagent.governance.errors import GovernanceError, SandboxRequiredError
from omnicoreagent.sandbox import (
    LocalProcessSandboxRuntime,
    SandboxAuthorityContext,
    SandboxCommandSpec,
    SandboxExecRequest,
    SandboxExecutionService,
    SandboxManifest,
    SandboxUnsupportedError,
    build_sandbox_runtime,
)
from omnicoreagent.sandbox.execution import _sandbox_authority_request
from test_execute_tool import ScriptedModel, _MODEL

class DescribingModel(ScriptedModel):
    """Also keeps what each offered tool is described as."""

    descriptions: dict[str, str] = {}

    async def llm_call(self, messages, tools=None, **kwargs):
        self.descriptions = {t["function"]["name"]: t["function"]["description"] for t in tools or []}
        return await super().llm_call(messages, tools=tools, **kwargs)


AUTHORITY = SandboxAuthorityContext(authority_request_id="authreq_test", decision_id="decision_test")


def _runtime(**options):
    return build_sandbox_runtime({"provider": "local", "options": options})


def _manifest(working_dir, **fields):
    return SandboxManifest(
        working_dir=str(working_dir),
        network_policy={"default": "allow"},
        filesystem_policy={"default": "allow"},
        **fields,
    )


async def _run(runtime, session, *command, **request):
    return await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=list(command), authority=AUTHORITY, **request),
    )


@pytest_asyncio.fixture
async def session_of(tmp_path):
    opened = []

    async def open_(runtime, manifest=None):
        session = await runtime.create(manifest or _manifest(tmp_path))
        opened.append((runtime, session))
        return session

    yield open_
    for runtime, session in opened:
        await runtime.terminate(session.session_id)


# --- what it is ---------------------------------------------------------------


def test_the_local_provider_says_it_runs_on_the_host_without_isolation():
    runtime = _runtime()

    assert isinstance(runtime, LocalProcessSandboxRuntime)
    assert runtime.provider == "local"
    assert runtime.supports_execution is True
    assert runtime.supports_required_sandbox is False
    assert runtime.execution_surface == "host"


def test_unknown_or_malformed_options_are_refused():
    with pytest.raises(ValueError, match="Unknown local sandbox option"):
        _runtime(image="python:3.12")
    with pytest.raises(ValueError, match="true or false"):
        _runtime(inherit_environment="yes")


# --- running commands ---------------------------------------------------------


@pytest.mark.asyncio
async def test_commands_run_in_the_working_directory_with_exit_code_and_output(session_of, tmp_path):
    runtime = _runtime()
    session = await session_of(runtime)

    ok = await _run(runtime, session, "sh", "-c", "pwd; echo err >&2")
    failed = await _run(runtime, session, "sh", "-c", "exit 3")
    piped = await _run(runtime, session, "cat", stdin="from stdin")
    missing = await _run(runtime, session, "no-such-program-omnicoreagent")

    assert ok.exit_code == 0 and ok.stdout.strip() == os.path.realpath(tmp_path)
    assert ok.stderr.strip() == "err"
    assert failed.exit_code == 3 and failed.ok is False
    assert piped.stdout == "from stdin"
    assert missing.exit_code == 127


@pytest.mark.asyncio
async def test_files_a_command_writes_are_real_and_stay_after_the_session(session_of, tmp_path):
    runtime = _runtime()
    session = await session_of(runtime)

    await _run(runtime, session, "sh", "-c", "echo kept > state.txt")
    await runtime.terminate(session.session_id)

    # The working directory belongs to the user: closing the session leaves it.
    assert (tmp_path / "state.txt").read_text() == "kept\n"


@pytest.mark.asyncio
async def test_a_command_over_its_time_limit_is_killed_with_what_it_started(session_of, tmp_path):
    runtime = _runtime()
    session = await session_of(runtime)
    marker = tmp_path / "late"

    started = time.monotonic()
    slow = await _run(
        runtime, session, "sh", "-c", f"(sleep 3; touch {marker}) & sleep 30", timeout_seconds=1
    )
    after = await _run(runtime, session, "echo", "still here")

    assert slow.timed_out is True and slow.exit_code == 137 and slow.ok is False
    assert time.monotonic() - started < 10
    assert after.stdout.strip() == "still here"
    time.sleep(3.5)
    assert not marker.exists(), "the background child was killed with its group"


@pytest.mark.asyncio
async def test_output_is_bounded(session_of):
    runtime = _runtime(max_output_bytes=1000)
    session = await session_of(runtime)

    big = await _run(runtime, session, "sh", "-c", "head -c 50000 /dev/zero | tr '\\0' x")

    assert len(big.stdout.encode()) <= 1000
    assert big.metadata["stdout_truncated"] is True


@pytest.mark.asyncio
async def test_the_host_environment_stays_out_unless_inherited(session_of, tmp_path, monkeypatch):
    monkeypatch.setenv("HOST_ONLY_SECRET", "never-inside")
    manifest = _manifest(tmp_path, environment={"plain": {"GREETING": "hello"}})

    isolated_env = _runtime()
    plain = await _run(isolated_env, await session_of(isolated_env, manifest), "env")
    inheriting = _runtime(inherit_environment=True)
    inherited = await _run(inheriting, await session_of(inheriting, manifest), "env")

    assert "GREETING=hello" in plain.stdout and "PATH=" in plain.stdout
    assert "never-inside" not in plain.stdout
    assert "HOST_ONLY_SECRET=never-inside" in inherited.stdout


@pytest.mark.asyncio
async def test_closing_the_session_kills_a_command_still_running(tmp_path):
    import asyncio

    runtime = _runtime()
    session = await runtime.create(_manifest(tmp_path))
    running = asyncio.create_task(_run(runtime, session, "sleep", "30"))
    await asyncio.sleep(0.3)

    started = time.monotonic()
    await runtime.terminate(session.session_id)
    result = await asyncio.wait_for(running, timeout=5)

    assert time.monotonic() - started < 5
    assert result.exit_code == 137


# --- files through the runtime ------------------------------------------------


@pytest.mark.asyncio
async def test_files_move_in_and_out_inside_the_working_directory_only(session_of, tmp_path):
    runtime = _runtime()
    session = await session_of(runtime)
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside)

    await runtime.upload_files(session.session_id, {"data/in.txt": b"payload"})
    result = await _run(runtime, session, "sh", "-c", "tr a-z A-Z < data/in.txt > data/out.txt")
    files = await runtime.download_files(session.session_id, ["data/out.txt"])

    assert result.ok and files == {"data/out.txt": b"PAYLOAD"}
    for path in ("/etc/passwd", "../outside.txt", "link/escaped.txt"):
        with pytest.raises(PermissionError, match="outside"):
            await runtime.write_file(session.session_id, path, b"x")
    assert list(outside.iterdir()) == []


# --- what it refuses ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"network_policy": {"default": "deny"}}, "restrict the network"),
        ({"network_policy": {"default": "allow", "denied_hosts": ["example.com"]}}, "restrict the network"),
        ({"filesystem_policy": {"default": "deny"}}, "limit which files"),
        ({"filesystem_policy": {"default": "allow", "denied_paths": ["/etc"]}}, "limit which files"),
        ({"image": "python:3.12-slim"}, "an image"),
        ({"workspace_mount": {"source": "/srv/data", "target": "/data"}}, "a workspace mount"),
        ({"resources": {"memory": "1g"}}, "CPU, memory or GPU"),
        ({"resources": {"timeout_seconds": 60}}, "a sandbox lifetime"),
        ({"environment": {"secret_refs": ["vault/token"]}}, "secret references"),
    ],
)
async def test_a_manifest_asking_for_isolation_it_cannot_give_is_refused(tmp_path, fields, reason):
    manifest = {
        "working_dir": str(tmp_path),
        "network_policy": {"default": "allow"},
        "filesystem_policy": {"default": "allow"},
        **fields,
    }

    with pytest.raises(SandboxUnsupportedError, match=reason):
        await _runtime().create(SandboxManifest(**manifest))


@pytest.mark.asyncio
async def test_the_default_manifest_is_refused_because_it_asks_for_no_network():
    with pytest.raises(SandboxUnsupportedError, match="cannot restrict the network"):
        await _runtime().create(SandboxManifest())


@pytest.mark.asyncio
async def test_a_missing_working_directory_is_an_error_unless_creating_it_is_asked_for(tmp_path):
    missing = tmp_path / "work" / "dir"

    with pytest.raises(SandboxUnsupportedError, match="does not exist"):
        await _runtime().create(_manifest(missing))
    runtime = _runtime(create_working_dir=True)
    session = await runtime.create(_manifest(missing))

    assert missing.is_dir()
    await runtime.terminate(session.session_id)


# --- governance ---------------------------------------------------------------


def _policy(*rules: PolicyRule) -> PolicyEnvelope:
    return PolicyEnvelope(name="local", mode=PolicyMode.STRICT, rules=PolicyRuleSet(allow=list(rules)))


ALLOW_SETUP = PolicyRule(rule_id="allow_setup", effect=PolicyEffect.ALLOW, capability="sandbox.*")
ALLOW_HOST = PolicyRule(
    rule_id="allow_host_commands",
    effect=PolicyEffect.ALLOW,
    capability="process.exec",
    conditions=PolicyRuleConditions(execution_surface="host"),
)


@pytest.mark.asyncio
async def test_commands_are_authorized_as_host_execution_and_run_when_a_rule_allows_it(tmp_path):
    engine = GovernanceEngine(_policy(ALLOW_SETUP, ALLOW_HOST), sandbox_runtime=_runtime())

    result = await SandboxExecutionService(engine).execute(
        SandboxCommandSpec(command=["sh", "-c", "echo governed > out.txt"], manifest=_manifest(tmp_path))
    )

    assert result.ok and (tmp_path / "out.txt").read_text() == "governed\n"
    assert result.metadata["sandbox_provider"] == "local"
    assert result.metadata["execution_surface"] == "host"
    assert result.metadata["authority"]["matched_rule_ids"] == ["allow_host_commands"]


@pytest.mark.asyncio
async def test_a_rule_that_allows_only_sandboxed_execution_does_not_cover_it(tmp_path):
    sandbox_only = PolicyRule(
        rule_id="allow_sandboxed_process",
        effect=PolicyEffect.ALLOW,
        capability="process.exec",
        constraints=PolicyConstraints(sandbox_required=True),
    )
    engine = GovernanceEngine(_policy(ALLOW_SETUP, sandbox_only), sandbox_runtime=_runtime())

    with pytest.raises(SandboxRequiredError):
        await SandboxExecutionService(engine).execute(
            SandboxCommandSpec(command=["touch", "never"], manifest=_manifest(tmp_path))
        )
    assert not (tmp_path / "never").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "effect", "rule"),
    [
        ("permissive-dev", PolicyEffect.DENY, "deny_unrestricted_process_exec"),
        ("interactive-dev", PolicyEffect.ASK, "ask_process_exec"),
        ("strict-production", PolicyEffect.DENY, None),
    ],
)
async def test_the_built_in_profiles_deny_or_ask_about_host_commands(profile, effect, rule):
    engine = GovernanceEngine(build_default_policy(profile), sandbox_runtime=_runtime())
    request = _sandbox_authority_request(SandboxCommandSpec(command=["touch", "x"]), "host")

    decision = await engine.evaluate(request)

    assert request.execution_surface == "host"
    assert decision.effect == effect
    if rule:
        assert rule in decision.matched_rule_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["permissive-dev", "interactive-dev", "strict-production"])
async def test_under_the_built_in_profiles_nothing_runs_without_a_rule(tmp_path, profile):
    engine = GovernanceEngine(build_default_policy(profile), sandbox_runtime=_runtime())

    with pytest.raises(GovernanceError):
        await SandboxExecutionService(engine).execute(
            SandboxCommandSpec(command=["touch", "never"], manifest=_manifest(tmp_path))
        )
    assert not (tmp_path / "never").exists()


def test_the_engine_routes_commands_to_it_without_calling_it_a_sandbox():
    engine = GovernanceEngine(build_default_policy("interactive-dev"), sandbox_runtime=_runtime())

    assert engine.sandbox_runtime_can_execute() is True
    assert engine._sandbox_runtime_satisfies_required_boundary() is False


# --- an agent using it --------------------------------------------------------


async def _agent(model, tmp_path, *, policy=None, profile="interactive-dev"):
    governance = {
        "enabled": True,
        "sandbox_config": {"provider": "local"},
        "sandbox_manifest": {
            "working_dir": str(tmp_path),
            "network_policy": {"default": "allow"},
            "filesystem_policy": {"default": "allow"},
        },
    }
    if policy is not None:
        governance["policy"] = policy
    else:
        governance["profile"] = profile
    agent = OmniCoreAgent(
        name="local-agent",
        system_instruction="Use execute to run commands.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": governance,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _agent_policy():
    policy = build_default_policy("interactive-dev")
    # The asks that stand between an agent and host commands, for a machine
    # that is itself the boundary (a disposable container).
    policy.rules.ask = [
        rule
        for rule in policy.rules.ask
        if rule.rule_id not in {"ask_process_exec", "ask_high_risk", "ask_sandbox_network"}
    ]
    policy.rules.allow.insert(0, ALLOW_HOST)
    return policy


@pytest.mark.asyncio
async def test_an_agent_runs_commands_on_the_host_and_is_told_it_is_not_isolated(tmp_path):
    model = DescribingModel(
        [("c1", "execute", '{"command": "echo from-agent > made.txt && cat made.txt"}')],
        "done",
    )
    agent = await _agent(model, tmp_path, policy=_agent_policy())

    result = await agent.run("go", session_id="local")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    assert "execute" in model.tools_offered[0]
    assert (tmp_path / "made.txt").read_text() == "from-agent\n"
    [tool_result] = [e.output for e in trace.events if e.event_type == "tool_result"]
    assert tool_result["data"]["execution_surface"] == "host"
    assert "not isolated" in model.descriptions["execute"]
    assert "isolated sandbox" not in model.descriptions["execute"]
    assert any(w["code"] == "host_execution_not_contained" for w in agent._security_warnings())


@pytest.mark.asyncio
async def test_with_the_interactive_profile_each_host_command_waits_for_a_person(tmp_path):
    model = ScriptedModel(
        [("c1", "execute", '{"command": "touch approved.txt"}')],
        "done",
    )
    agent = await _agent(model, tmp_path, profile="interactive-dev")

    result = await agent.run("go", session_id="asks")

    assert result["status"] == "awaiting_approval"
    assert not (tmp_path / "approved.txt").exists()
    capabilities = {a["capability"] for a in result["approvals"]}
    assert capabilities & {"process.exec", "sandbox.network.configure"}


@pytest.mark.asyncio
async def test_with_the_permissive_profile_the_command_is_refused_and_nothing_runs(tmp_path):
    model = ScriptedModel(
        [("c1", "execute", '{"command": "touch refused.txt"}')],
        "done",
    )
    agent = await _agent(model, tmp_path, profile="permissive-dev")

    await agent.run("go", session_id="denied")

    assert not (tmp_path / "refused.txt").exists()


@pytest.mark.asyncio
async def test_a_skill_script_runs_in_place_as_a_host_command_copying_nothing(tmp_path):
    from omnicoreagent.sandbox.scope import ExecutionScope
    from test_execute_tool import _run_skill, _skill

    work = tmp_path / "work"
    work.mkdir()
    manager = _skill(tmp_path, 'echo "ran in $(basename "$PWD")"\n')
    engine = GovernanceEngine(_policy(ALLOW_SETUP, ALLOW_HOST), sandbox_runtime=_runtime())

    async with ExecutionScope(SandboxExecutionService(engine), _manifest(work)).active():
        result = await _run_skill(manager)

    assert result["status"] == "success", result
    assert result["data"]["execution_surface"] == "host"
    assert result["data"]["stdout"].strip() == "ran in greeter"
    assert list(work.iterdir()) == [], "nothing was copied into the working directory"


# --- what a task needs of it, and what it still will not do -------------------


@pytest.mark.asyncio
async def test_named_variables_pass_through_without_the_rest_of_the_environment(
    session_of, tmp_path, monkeypatch
):
    """A task needs PYTHONPATH; it does not need the agent's provider key.

    Inheriting the whole environment is the blunt option and stays available;
    this is the one an evaluation harness can use without handing every
    credential this process holds to the model's commands.
    """
    monkeypatch.setenv("PYTHONPATH", "/task/lib")
    monkeypatch.setenv("LLM_API_KEY", "not-for-commands")
    runtime = _runtime(environment_passthrough=["PYTHONPATH"])
    session = await session_of(runtime)

    result = await _run(runtime, session, "sh", "-c", "echo $PYTHONPATH:$LLM_API_KEY")

    assert result.stdout.strip() == "/task/lib:"


def test_a_passthrough_that_is_not_a_list_of_names_is_refused():
    with pytest.raises(ValueError, match="environment_passthrough"):
        _runtime(environment_passthrough=[""])
    with pytest.raises(ValueError, match="environment_passthrough"):
        _runtime(environment_passthrough=[3])


@pytest.mark.asyncio
async def test_creating_a_session_leaves_the_callers_manifest_alone(tmp_path):
    runtime = _runtime()
    manifest = _manifest(tmp_path)
    before = manifest.provider

    session = await runtime.create(manifest)
    try:
        assert manifest.provider == before, "the caller's manifest was changed"
        assert session.manifest.provider == "local"
    finally:
        await runtime.terminate(session.session_id)


@pytest.mark.asyncio
async def test_a_command_with_a_working_directory_that_is_not_there_says_so(
    session_of, tmp_path
):
    """Told apart from a command that does not exist: both are FileNotFoundError."""
    runtime = _runtime()
    session = await session_of(runtime)

    missing = await _run(runtime, session, "pwd", cwd=str(tmp_path / "nowhere"))
    assert missing.exit_code == 127
    assert "does not exist" in missing.stderr

    unknown = await _run(runtime, session, "there-is-no-such-command")
    assert unknown.exit_code == 127
    assert "does not exist" not in unknown.stderr


@pytest.mark.asyncio
async def test_a_command_started_as_the_session_closes_does_not_outlive_it(
    tmp_path, monkeypatch
):
    """The window between starting a process and tracking it, closed.

    Terminating a session kills the commands it knows about. A command that
    started while the session was closing was not yet one of them, and would
    have been left running with nobody to stop it.
    """
    runtime = _runtime()
    session = await runtime.create(_manifest(tmp_path))
    marker = tmp_path / "survived"
    original = asyncio.create_subprocess_exec

    async def close_the_session_mid_start(*args, **kwargs):
        process = await original(*args, **kwargs)
        await runtime.terminate(session.session_id)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", close_the_session_mid_start)

    with pytest.raises(SandboxUnsupportedError):
        await _run(
            runtime,
            session,
            "sh",
            "-c",
            f"sleep 0.4; echo here > {marker}",
            timeout_seconds=10,
        )
    await asyncio.sleep(0.7)

    assert not marker.exists(), "a command outlived the session that started it"


@pytest.mark.asyncio
async def test_the_runtimes_own_writes_refuse_a_link_put_in_the_way(
    session_of, tmp_path, monkeypatch
):
    """The check and the open are not one step, so the open refuses a link.

    A path is checked after resolving links. Between that check and the write,
    a link can be put in its place — this stands in for that timing, and the
    write must refuse rather than follow it out of the directory.
    """
    runtime = _runtime()
    session = await session_of(runtime)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("before")
    original = runtime._inside_workdir

    def link_it_after_the_check(session_id, path):
        target = original(session_id, path)
        if not os.path.islink(target):
            os.symlink(outside, target)
        return target

    monkeypatch.setattr(runtime, "_inside_workdir", link_it_after_the_check)

    with pytest.raises(OSError):
        await runtime.write_file(session.session_id, "note.txt", b"after")

    assert outside.read_text() == "before", "a write followed a link out"


@pytest.mark.asyncio
async def test_the_workspace_is_not_copied_into_the_directory_commands_run_in(
    tmp_path,
):
    """On this machine there is nothing to copy the workspace into.

    A sandbox never sees the workspace, so files are copied in before each
    command and back after it. Here the working directory is a real directory
    on this machine — an evaluation task's own directory, say — and copying the
    agent's workspace into it would write over whatever is already there.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.md").write_text("the agent's own notes")
    working = tmp_path / "task"
    working.mkdir()
    (working / "notes.md").write_text("the task's notes")

    model = ScriptedModel(
        [("c1", "execute", '{"command": "cat notes.md"}')],
        "done",
    )
    governance = {
        "enabled": True,
        "policy": _agent_policy(),
        "sandbox_config": {"provider": "local"},
        "sandbox_manifest": {
            "working_dir": str(working),
            "network_policy": {"default": "allow"},
            "filesystem_policy": {"default": "allow"},
        },
    }
    agent = OmniCoreAgent(
        name="local-agent",
        system_instruction="Use execute to run commands.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(workspace)},
            "governance_config": governance,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model

    result = await agent.run("go", session_id="bridge")

    assert result["status"] == "success"
    # The command reads the directory's own file, not one copied over it.
    assert (working / "notes.md").read_text() == "the task's notes", (
        "the agent's workspace was copied over the directory commands run in"
    )
    # And the directory's files are not command output to be taken back: the
    # agent's own notes are still the agent's.
    assert (workspace / "notes.md").read_text() == "the agent's own notes", (
        "files that were already in the working directory were copied into "
        "the workspace as if the command had made them"
    )
    [tool_result] = [
        event.output
        for event in (
            await agent.telemetry_store.get_trace(result["trace_id"])
        ).events
        if event.event_type == "tool_result"
    ]
    assert not tool_result["data"].get("workspace_files", {}).get("written")
