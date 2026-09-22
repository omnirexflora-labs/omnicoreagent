"""The `execute` tool and skill scripts, end to end.

Sandbox runs use the real Docker backend (`alpine:3.20`) and are skipped, with
the reason, only where Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.skills.manager import SkillManager
from omnicoreagent.core.skills.tools import build_skill_tools
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


needs_docker = pytest.mark.skipif(not _docker_available(), reason="the Docker daemon is not reachable")
_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.tools_offered: list[set[str]] = []

    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        self.tools_offered.append({t["function"]["name"] for t in tools or []})
        usage = Usage(requests=1, request_tokens=10, response_tokens=2, total_tokens=12)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            return ModelTurn(content=turn, finish_reason="stop", usage=usage)
        return ModelTurn(
            tool_calls=tuple(ToolRequest(*call) for call in turn), finish_reason="tool_calls", usage=usage
        )


def _containers() -> set[str]:
    import docker

    return {c.id for c in docker.from_env().containers.list(all=True, filters={"label": "omnicoreagent.sandbox"})}


async def _agent(model, *, sandbox: bool, profile: str = "interactive-dev", **agent_config):
    governance = {"enabled": True, "profile": profile}
    if sandbox:
        governance["sandbox_config"] = {"provider": "docker", "options": {"image": "alpine:3.20"}}
    agent = OmniCoreAgent(
        name="exec-agent",
        system_instruction="Use execute to run commands.",
        model_config=_MODEL,
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": governance,
            **agent_config,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


@needs_docker
@pytest.mark.asyncio
async def test_execute_runs_in_one_sandbox_per_run_and_it_is_gone_afterwards():
    before = _containers()
    model = ScriptedModel(
        [("c1", "execute", '{"command": "echo kept > state.txt && echo first"}')],
        [("c2", "execute", '{"command": "cat state.txt; exit 4"}')],
        "done",
    )
    agent = await _agent(model, sandbox=True)

    result = await agent.run("go", session_id="exec")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    assert "execute" in model.tools_offered[0]
    results = [e.output for e in trace.events if e.event_type == "tool_result"]
    first, second = results[0]["data"], [e for e in trace.events if e.event_type in {"tool_result", "tool_error"}][1]
    assert first["stdout"].strip() == "first" and first["exit_code"] == 0
    # The file written by the first command is there for the second.
    assert "kept" in json_dump(second.output)
    assert _containers() == before
    decisions = [
        e.metadata["matched_rule_ids"]
        for e in trace.events
        if e.event_type.startswith("policy_decision_") and e.metadata.get("capability") == "process.exec"
    ]
    assert decisions and all(ids == ["allow_sandboxed_execution"] for ids in decisions)


def json_dump(value) -> str:
    import json

    return json.dumps(value, default=str)


@pytest.mark.asyncio
async def test_execute_is_not_offered_without_a_sandbox():
    model = ScriptedModel("done")
    agent = await _agent(model, sandbox=False)

    await agent.run("go", session_id="no-exec")

    assert "execute" not in model.tools_offered[0]
    assert agent.can_execute is False


def _skill(tmp_path, body: str, name: str = "greeter"):
    root = tmp_path / "skills"
    (root / name / "scripts").mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Says hello.\n---\nRun hello.sh.\n")
    script = root / name / "scripts" / "hello.sh"
    script.write_text(body)
    return SkillManager(root)


async def _run_skill(manager, **args):
    registry = build_skill_tools(skill_manager=manager, registry=ToolRegistry())
    return await registry.execute_tool("run_skill_script", {"skill_name": "greeter", "script_name": "hello.sh", **args})


@pytest.mark.asyncio
async def test_a_skill_script_on_the_host_runs_without_blocking_the_event_loop(tmp_path):
    manager = _skill(tmp_path, "sleep 1; echo hello from host\n")
    gaps = []

    async def heartbeat():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            gaps.append(time.monotonic() - last)
            last = time.monotonic()

    beat = asyncio.create_task(heartbeat())
    try:
        result = await _run_skill(manager)
    finally:
        beat.cancel()

    assert result["status"] == "success"
    assert result["data"]["stdout"].strip() == "hello from host"
    assert result["data"]["execution_surface"] == "host"
    assert max(gaps) < 0.5, max(gaps)


@pytest.mark.asyncio
async def test_a_skill_script_over_its_time_limit_is_killed(tmp_path):
    manager = _skill(tmp_path, "sleep 30\n")

    started = time.monotonic()
    result = await _run_skill(manager, timeout=1)

    assert result["status"] == "error" and "timed out" in result["message"].lower()
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_a_skill_name_cannot_reach_a_sibling_directory(tmp_path):
    manager = _skill(tmp_path, "echo safe\n")
    evil = tmp_path / "skills-evil"
    (evil / "scripts").mkdir(parents=True)
    (evil / "SKILL.md").write_text("---\nname: evil\ndescription: x\n---\n")
    (evil / "scripts" / "hello.sh").write_text("echo escaped\n")

    registry = build_skill_tools(skill_manager=manager, registry=ToolRegistry())
    result = await registry.execute_tool(
        "run_skill_script", {"skill_name": "../skills-evil", "script_name": "hello.sh"}
    )

    assert result["status"] == "error"
    assert "escaped" not in json_dump(result)


@needs_docker
@pytest.mark.asyncio
async def test_a_skill_script_runs_in_the_sandbox_when_one_is_configured(tmp_path):
    from omnicoreagent.sandbox import SandboxExecutionService, build_sandbox_runtime
    from omnicoreagent.sandbox.scope import ExecutionScope

    from omnicoreagent.governance import GovernanceEngine, build_default_policy

    manager = _skill(tmp_path, "echo \"inside $(cat /etc/alpine-release)\"\n")
    runtime = build_sandbox_runtime({"provider": "docker", "options": {"image": "alpine:3.20"}})
    engine = GovernanceEngine(build_default_policy("interactive-dev"), sandbox_runtime=runtime)
    before = _containers()

    async with ExecutionScope(SandboxExecutionService(engine)).active():
        result = await _run_skill(manager)

    assert result["status"] == "success"
    assert result["data"]["execution_surface"] == "sandbox"
    assert result["data"]["stdout"].startswith("inside 3.20")
    assert _containers() == before


@needs_docker
@pytest.mark.asyncio
async def test_a_cancelled_run_leaves_no_container_behind():
    before = _containers()
    model = ScriptedModel([("c1", "execute", '{"command": "sleep 30", "timeout": 60}')], "done")
    agent = await _agent(model, sandbox=True)

    run = asyncio.create_task(agent.run("go", session_id="cancel"))
    for _ in range(100):
        if _containers() - before:
            break
        await asyncio.sleep(0.1)
    assert _containers() - before, "the sandbox never started"
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert _containers() == before


@pytest.mark.asyncio
async def test_a_host_skill_script_gets_a_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("APP_SERVICE_URL", "https://svc.example")
    manager = _skill(tmp_path, "env\n")

    result = await _run_skill(manager)

    names = {line.split("=", 1)[0] for line in result["data"]["stdout"].splitlines()}
    assert "sk-must-not-leak" not in result["data"]["stdout"]
    assert "APP_SERVICE_URL" not in names
    assert "PATH" in names


@pytest.mark.asyncio
async def test_a_host_skill_script_gets_the_variables_the_application_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("APP_SERVICE_URL", "https://svc.example")
    manager = _skill(tmp_path, "env\n")
    registry = build_skill_tools(
        skill_manager=manager, registry=ToolRegistry(), env_passthrough=["APP_SERVICE_URL"]
    )

    result = await registry.execute_tool("run_skill_script", {"skill_name": "greeter", "script_name": "hello.sh"})

    assert "APP_SERVICE_URL=https://svc.example" in result["data"]["stdout"]
    assert "sk-must-not-leak" not in result["data"]["stdout"]


def test_the_agent_config_names_the_variables_skill_scripts_receive():
    from omnicoreagent.core.runtime.config import AgentConfig

    assert AgentConfig(skill_script_env=["APP_SERVICE_URL"]).skill_script_env == ["APP_SERVICE_URL"]
    with pytest.raises(ValueError, match="skill_script_env"):
        AgentConfig(skill_script_env="APP_SERVICE_URL")


@pytest.mark.asyncio
async def test_an_agents_skill_scripts_receive_only_the_configured_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("APP_SERVICE_URL", "https://svc.example")
    monkeypatch.chdir(tmp_path)
    skill = tmp_path / ".agents" / "skills" / "greeter"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: greeter\ndescription: Says hello.\n---\nRun hello.sh.\n")
    (skill / "scripts" / "hello.sh").write_text("env\n")
    model = ScriptedModel(
        [("c1", "run_skill_script", '{"skill_name": "greeter", "script_name": "hello.sh"}')], "done"
    )
    agent = await _agent(
        model, sandbox=False, enable_agent_skills=True, skill_script_env=["APP_SERVICE_URL"]
    )

    result = await agent.run("go", session_id="skill-env")
    trace = await agent.telemetry_store.get_trace(result["trace_id"])

    output = json_dump([e.output for e in trace.events if e.event_type == "tool_result"])
    assert "APP_SERVICE_URL=https://svc.example" in output
    assert "sk-must-not-leak" not in output
