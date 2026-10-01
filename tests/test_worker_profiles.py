"""Worker profiles: the lead picks a model, effort, tools and narrower rules
for each worker it spawns (engineering/architecture/worker-profiles-plan.md).
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.runtime.config import normalize_agent_config
from omnicoreagent.core.worker_profiles import WorkerProfile, worker_profiles_from_value

EXPLORER = {
    "name": "explorer",
    "description": "Reads and searches the workspace.",
    "model_config": {"model": "gpt-5.4-mini"},
    "reasoning_effort": "low",
    "tools": ["read_file", "grep"],
    "max_steps": 20,
}


# P1 — the setting and its checks


def test_profiles_are_read_from_the_agent_config():
    config = normalize_agent_config("lead", {"enable_subagents": True, "worker_profiles": [EXPLORER]})
    (profile,) = worker_profiles_from_value(config["worker_profiles"])
    assert isinstance(profile, WorkerProfile)
    assert profile.name == "explorer" and profile.max_steps == 20
    assert profile.reasoning_effort == "low" and profile.tools == ["read_file", "grep"]


def test_no_profiles_is_the_default():
    assert normalize_agent_config("lead", {})["worker_profiles"] == []


@pytest.mark.parametrize(
    ("profiles", "message"),
    [
        ([{**EXPLORER, "modle": "x"}], "unknown keys: modle"),
        ([EXPLORER, EXPLORER], "more than once"),
        ([{**EXPLORER, "name": "Has Space"}], "name"),
        ([{**EXPLORER, "description": " "}], "description"),
        ([{k: v for k, v in EXPLORER.items() if k != "description"}], "description"),
        ([{**EXPLORER, "max_steps": 0}], "max_steps"),
        ([{**EXPLORER, "max_steps": 51}], "max_steps"),
        ([{**EXPLORER, "reasoning_effort": "huge"}], "reasoning_effort"),
        ([{**EXPLORER, "tools": "read_file"}], "tools"),
        ([{**EXPLORER, "policy": {"allow": [{"capability": "network.http"}]}}], "only narrow"),
        ([{**EXPLORER, "policy": {"deny": [{"capability": "netwrok.http"}]}}], "netwrok"),
        ([{**EXPLORER, "model_config": {"provider": "anthropic"}}], "model"),
    ],
)
def test_a_bad_profile_is_refused_when_the_config_is_built(profiles, message):
    with pytest.raises(ValueError, match=message):
        normalize_agent_config("lead", {"enable_subagents": True, "worker_profiles": profiles})


def test_profiles_need_subagents_on():
    with pytest.raises(ValueError, match="enable_subagents"):
        normalize_agent_config("lead", {"worker_profiles": [EXPLORER]})


# P2 — the spawn tool offers the profiles


def _factory(profiles):
    from omnicoreagent.core.subagents import SubagentFactory
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    return SubagentFactory(
        base_model_config={"provider": "openai", "model": "gpt-5.4", "api_key": "k"},
        local_tools=ToolRegistry(),
        agent_config={"enable_subagents": True, "worker_profiles": profiles},
    )


def _spawn_tool(factory):
    from omnicoreagent.core.subagents import build_subagent_tools
    from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

    registry = ToolRegistry()
    build_subagent_tools(factory, registry)
    return registry.get_tool("spawn_subagents")


BUILDER = {"name": "builder", "description": "Writes and tests code.", "reasoning_effort": "high"}


def test_the_lead_is_offered_each_profile_by_name_and_description():
    tool = _spawn_tool(_factory([EXPLORER, BUILDER]))
    item = tool.inputSchema["properties"]["subagents"]["items"]
    assert item["properties"]["profile"]["enum"] == ["explorer", "builder"]
    assert "profile" in item["required"]
    assert "explorer" in tool.description and "Reads and searches the workspace." in tool.description
    assert "gpt-5.4-mini" in tool.description and "high" in tool.description


def test_without_profiles_the_tool_is_as_before():
    item = _spawn_tool(_factory([])).inputSchema["properties"]["subagents"]["items"]
    assert "profile" not in item["properties"]
    assert item["required"] == ["name", "role", "task", "output_path"]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "reviewer"])
async def test_a_worker_without_a_known_profile_is_not_started(profile):
    factory = _factory([EXPLORER, BUILDER])
    started = []

    async def run_subagent(**kw):  # pragma: no cover - must not be reached
        started.append(kw)
        return {"status": "success", "data": {}}

    factory.run_subagent = run_subagent
    spec = {"name": "w", "role": "r", "task": "t", "output_path": "w.md"}
    if profile:
        spec["profile"] = profile
    result = await _spawn_tool(factory).execute({"subagents": [spec]})

    assert result["status"] == "error" and not started
    assert "explorer" in result["message"] and "builder" in result["message"]


# P3 — the worker is built from its profile


def _lead_agent(tmp_path, profiles, **extra):
    from omnicoreagent import OmniCoreAgent

    return OmniCoreAgent(
        name="lead",
        system_instruction="x",
        model_config={"provider": "openai", "model": "gpt-5.4", "api_key": "lead-key"},
        agent_config={
            "guardrail_mode": "off",
            "enable_subagents": True,
            "worker_profiles": profiles,
            "workspace_config": {"workspace_dir": str(tmp_path / "ws")},
            **extra,
        },
    )


@pytest.mark.asyncio
async def test_a_worker_gets_its_profiles_model_effort_steps_and_tools(tmp_path):
    lead = _lead_agent(tmp_path, [{**EXPLORER, "instructions": "Never edit files."}, BUILDER])
    await lead.initialize()
    try:
        worker = lead._subagent_factory.create_subagent(
            name="w", role="search", task="find x", output_path="w.md", profile="explorer"
        )
        await worker.initialize()
        tools = {tool["name"] for tool in await worker.list_all_available_tools()}
        model = worker.model_config
        steps = worker.agent_config["max_steps"]
        instruction = worker.system_instruction
        await worker.cleanup()
    finally:
        await lead.cleanup()

    assert model["model"] == "gpt-5.4-mini" and model["reasoning_effort"] == "low"
    assert model["api_key"] == "lead-key", "the same provider keeps the lead's key"
    assert steps == 20
    assert tools == {"read_file", "grep", "write_file"}, tools
    assert "Never edit files." in instruction


@pytest.mark.asyncio
async def test_a_profile_on_another_provider_does_not_get_the_leads_key(tmp_path):
    other = {"name": "other", "description": "d",
             "model_config": {"provider": "anthropic", "model": "claude-sonnet-5"}}
    lead = _lead_agent(tmp_path, [other])
    await lead.initialize()
    try:
        worker = lead._subagent_factory.create_subagent(
            name="w", role="r", task="t", output_path="w.md", profile="other"
        )
        model = worker.model_config
    finally:
        await lead.cleanup()

    assert model["provider"] == "anthropic" and model["model"] == "claude-sonnet-5"
    assert model.get("api_key") in (None, "") and "lead-key" not in str(model)


@pytest.mark.asyncio
async def test_a_worker_without_a_profile_is_as_the_lead(tmp_path):
    lead = _lead_agent(tmp_path, [])
    await lead.initialize()
    try:
        worker = lead._subagent_factory.create_subagent(name="w", role="r", task="t", output_path="w.md")
        await worker.initialize()
        tools = {tool["name"] for tool in await worker.list_all_available_tools()}
        model = worker.model_config
        await worker.cleanup()
    finally:
        await lead.cleanup()

    assert model["model"] == "gpt-5.4"
    assert {"read_file", "write_file", "edit_file", "delete_file"} <= tools


@pytest.mark.asyncio
async def test_a_profile_naming_a_tool_the_lead_lacks_is_refused_at_initialize(tmp_path):
    lead = _lead_agent(tmp_path, [{**EXPLORER, "tools": ["read_file", "web_search"]}])
    with pytest.raises(ValueError, match="web_search"):
        await lead.initialize()
    await lead.cleanup()
