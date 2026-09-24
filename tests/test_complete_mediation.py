"""Complete mediation: nothing the agent does has a side effect governance did not allow.

Governance is a decision layer; it only binds if every side-effecting path goes
through it. These tests fail when that stops being true:

- a static scan: the library may start processes or open raw sockets only at
  the reviewed sites listed here, each with the reason it is safe;
- a deny-everything run: the agent tries every kind of tool (application tool,
  workspace write, host skill script, sandboxed command, delegation) under a
  policy with no allow rules, and no process starts and no file changes;
- the labels governance sees: each built-in tool presents the capability,
  execution surface, and risk a policy needs to tell it apart;
- delegation: a governed agent cannot hand work to an ungoverned one.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance.capabilities import tool_authority_requests
from test_execute_tool import ScriptedModel, _containers, _MODEL, _docker_available

SRC = Path(__file__).resolve().parents[1] / "src" / "omnicoreagent"

# Every call that can start a process or open a raw socket, by module and
# function, with why it is safe. Adding a site means adding it here, reviewed.
REVIEWED_SITES = {
    ("core/skills/tools.py", "asyncio.create_subprocess_exec"): (
        "host skill scripts; authorized as skill.script.run (surface host, "
        "risk high) before this runs, and only when the run has no sandbox"
    ),
    ("sandbox/local_process.py", "asyncio.create_subprocess_exec"): (
        "the local sandbox's commands; reached only through the governed "
        "execution service, which authorizes each as process.exec on surface "
        "host (never satisfying a rule that requires a sandbox) before it runs"
    ),
    ("mcp_clients_connection/oauth.py", "socket.socket"): (
        "binds a loopback port to find a free one for the OAuth callback; "
        "sends and receives nothing"
    ),
    ("mcp_clients_connection/transports.py", "stdio_client"): (
        "starts an MCP server the application configured (command, args, env "
        "from application config, never from the model)"
    ),
}
_PROCESS_OR_SOCKET = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.system",
    "os.popen",
    "os.fork",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execl",
    "os.spawnv",
    "os.spawnl",
    "pty.spawn",
    "socket.socket",
    "socket.create_connection",
    "stdio_client",
}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return None


def test_processes_and_raw_sockets_are_started_only_at_reviewed_sites():
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in _PROCESS_OR_SOCKET:
                    found.add((str(path.relative_to(SRC)), name))

    assert found == set(REVIEWED_SITES), (
        "A process or socket site was added or removed; review it and update "
        f"REVIEWED_SITES: {sorted(found ^ set(REVIEWED_SITES))}"
    )


# --- a deny-everything run ------------------------------------------------


class _Spawns:
    """Records processes started while armed (audit hooks cannot be removed)."""

    armed = False
    seen: list[str] = []

    @classmethod
    def hook(cls, event, args):
        if cls.armed and event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
            cls.seen.append(f"{event}: {args[:2]!r}")


sys.addaudithook(_Spawns.hook)


def _app_tools(marker: Path) -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register_tool("write_marker", description="Writes a marker file.")
    def write_marker() -> dict:
        marker.write_text("application tool ran")
        return {"status": "success"}

    return tools


def _skill(root: Path, marker: Path) -> None:
    skill = root / ".agents" / "skills" / "marker"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: marker\ndescription: Writes a marker.\n---\nRun mark.sh.\n")
    (skill / "scripts" / "mark.sh").write_text(f"echo skill ran > {marker}\n")


@pytest.mark.asyncio
async def test_under_a_policy_with_no_allow_rules_no_tool_has_any_effect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_marker, skill_marker = tmp_path / "app.txt", tmp_path / "skill.txt"
    _skill(tmp_path, skill_marker)
    sandbox = _docker_available()
    governance = {"enabled": True, "profile": "strict-production"}
    if sandbox:
        governance["sandbox_config"] = {"provider": "docker", "options": {"image": "alpine:3.20"}}
    calls = [
        ("c1", "write_marker", "{}"),
        ("c2", "write_file", json.dumps({"path": "note.txt", "content": "x"})),
        ("c3", "run_skill_script", json.dumps({"skill_name": "marker", "script_name": "mark.sh"})),
    ]
    if sandbox:
        calls.append(("c4", "execute", json.dumps({"command": "echo hi > out.txt"})))
    model = ScriptedModel(calls, "done")
    agent = OmniCoreAgent(
        name="mediation",
        system_instruction="Try everything.",
        model_config=_MODEL,
        local_tools=_app_tools(app_marker),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "enable_agent_skills": True,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            "governance_config": governance,
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    before = _containers() if sandbox else set()

    _Spawns.seen.clear()
    _Spawns.armed = True
    try:
        result = await agent.run("go", session_id="mediation")
    finally:
        _Spawns.armed = False
    trajectory = await agent.get_trajectory(result["trace_id"])

    outcomes = {
        call["tool_name"]: call["outcome"]
        for step in trajectory["steps"]
        for call in step["tool_calls"]
    }
    assert outcomes == {name: "denied" for _, name, _ in calls}
    assert not app_marker.exists() and not skill_marker.exists()
    assert not (tmp_path / "ws" / "files" / "note.txt").exists()
    assert _Spawns.seen == []
    if sandbox:
        assert _containers() == before


# --- what governance sees --------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "provider", "capability", "surface", "risk"),
    [
        ("write_file", "workspace", "workspace.files.write", "workspace", "medium"),
        ("clear_files", "workspace", "workspace.files.clear", "workspace", "critical"),
        ("run_skill_script", "skill", "skill.script.run", "host", "high"),
        ("read_skill_file", "skill", "skill.files.read", "host", "low"),
        ("execute", "sandbox", "sandbox.execute", "sandbox", "high"),
        ("run_code", "code", "code.run", "code", "medium"),
        ("some_tool", "mcp", "tool.mcp.call", "mcp", "low"),
        ("my_tool", "local", "tool.local.call", "tool", "low"),
    ],
)
def test_each_built_in_tool_presents_what_a_policy_needs(tool_name, provider, capability, surface, risk):
    (request,) = tool_authority_requests(tool_name=tool_name, tool_args={"path": "x"}, tool_provider=provider)

    assert (request.capability, request.execution_surface, request.risk_level) == (capability, surface, risk)


@pytest.mark.asyncio
async def test_a_skill_script_in_a_sandboxed_run_is_labelled_as_sandboxed():
    from omnicoreagent.sandbox import LocalTestSandboxRuntime, SandboxExecutionService
    from omnicoreagent.sandbox.scope import ExecutionScope
    from omnicoreagent.governance import GovernanceEngine, build_default_policy

    engine = GovernanceEngine(
        build_default_policy("interactive-dev"),
        sandbox_runtime=LocalTestSandboxRuntime(),
        allow_test_sandbox_runtime=True,
    )
    async with ExecutionScope(SandboxExecutionService(engine)).active():
        (request,) = tool_authority_requests(
            tool_name="run_skill_script", tool_args={}, tool_provider="skill"
        )

    assert request.execution_surface == "sandbox"


# --- delegation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_governed_agent_cannot_delegate_to_an_ungoverned_one(tmp_path):
    marker = tmp_path / "child.txt"
    child = OmniCoreAgent(
        name="helper",
        system_instruction="Help.",
        model_config=_MODEL,
        local_tools=_app_tools(marker),
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await child.initialize()
    child.llm_connection = ScriptedModel([("k1", "write_marker", "{}")], "child done")
    parent = OmniCoreAgent(
        name="lead",
        system_instruction="Delegate.",
        model_config=_MODEL,
        sub_agents=[child],
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {
                "enabled": True,
                "profile": "permissive-dev",
            },
        },
        telemetry_config={"capture": "full"},
    )
    await parent.initialize()
    parent.llm_connection = ScriptedModel([("p1", "delegate_helper", '{"query": "do it"}')], "done")

    result = await parent.run("go", session_id="delegation")
    trajectory = await parent.get_trajectory(result["trace_id"])

    (call,) = [c for step in trajectory["steps"] for c in step["tool_calls"]]
    assert call["outcome"] in {"denied", "error"}
    assert "not governed" in json.dumps(call["result"]) + json.dumps(call["observation"])
    assert not marker.exists()


def test_a_spawned_worker_keeps_the_governance_labels_of_the_tools_it_inherits():
    from omnicoreagent.core.subagents import SubagentFactory
    from omnicoreagent.core.tools.execution_tools import build_execution_tools
    from omnicoreagent.core.skills.tools import build_skill_tools

    parent_tools = build_execution_tools(ToolRegistry(), max_timeout_seconds=30)
    build_skill_tools(skill_manager=object(), registry=parent_tools)

    worker_tools = SubagentFactory(base_model_config=_MODEL, local_tools=parent_tools)._build_subagent_local_tools()

    assert worker_tools.get_tool_provider("execute") == "sandbox"
    assert worker_tools.get_tool_provider("run_skill_script") == "skill"
    assert worker_tools.get_tool_provider("read_skill_file") == "skill"
