"""An ask on delegation pauses the lead (stranger test S4, 2026-09-26).

A research team built from the docs alone found that `subagent.spawn`
matching an ask came back to the lead as a tool error ("Matched ask policy
rule."), the run ended `success`, and a pending approval nobody could act on
was left on the record: it named no tool call, so the run never paused.
"""

from __future__ import annotations

import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.governance import build_default_policy
from test_run_suspend import RecordingModel

MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}
CONFIG = {"enable_workspace_files": False, "guardrail_mode": "off"}


async def _team():
    policy = build_default_policy("interactive-dev")  # asks before subagent.spawn
    governance = {"enabled": True, "policy": policy}
    child = OmniCoreAgent(
        name="researcher", system_instruction="Research.", model_config=MODEL,
        agent_config={**CONFIG, "governance_config": governance},
    )
    await child.initialize()
    child.llm_connection = RecordingModel([], "three sources")
    lead = OmniCoreAgent(
        name="lead", system_instruction="Lead.", model_config=MODEL, sub_agents=[child],
        agent_config={**CONFIG, "governance_config": governance},
    )
    await lead.initialize()
    lead.llm_connection = RecordingModel([("d1", "delegate_researcher", '{"query": "find"}')], "done")
    return lead, child


@pytest.mark.asyncio
async def test_an_ask_on_delegation_pauses_the_lead_and_resumes_into_the_child():
    lead, child = await _team()
    paused = await lead.run("research wind power", run_id="run_team")

    assert paused["status"] == "awaiting_approval"
    (approval,) = paused["approvals"]
    assert approval["tool_name"] == "delegate_researcher"
    assert child.llm_connection.calls == [], "nothing ran before a person decided"

    await lead.resolve_approval("run_team", approval["approval_id"], decision="approve", approver="alice")
    finished = await lead.resume("run_team")
    assert finished["status"] == "success"
    assert child.llm_connection.calls, "the child ran, after the approval"
    record = await lead.get_run("run_team")
    assert [a["status"] for a in record["approvals"]] == ["used"], "no orphaned ask is left behind"


@pytest.mark.parametrize("provider", ["docker", "e2b", "vercel", "local"])
def test_a_host_allowlist_a_provider_cannot_enforce_is_refused_when_the_agent_is_built(provider):
    """A code runner asked Docker for one allowed host: the agent was built,
    a person was asked to turn the network on, and only then was it refused."""
    with pytest.raises(ValueError, match="allowlist"):
        OmniCoreAgent(
            name="a", system_instruction="x", model_config=MODEL,
            agent_config={
                **CONFIG,
                "governance_config": {
                    "enabled": True,
                    "profile": "interactive-dev",
                    "sandbox_config": {"provider": provider},
                    "sandbox_manifest": {"network_policy": {"default": "deny", "allowed_hosts": ["pypi.org"]}},
                },
            },
        )


@pytest.mark.asyncio
async def test_the_delegation_tools_are_listed_with_the_others():
    """Round two: an agent with sub_agents listed no delegate_* tool, so the
    names could not be checked without a model run."""
    child = OmniCoreAgent(name="researcher", system_instruction="Research.", model_config=MODEL, agent_config=CONFIG)
    lead = OmniCoreAgent(name="lead", system_instruction="Lead.", model_config=MODEL, sub_agents=[child], agent_config=CONFIG)
    names = [tool["name"] for tool in await lead.list_all_available_tools()]
    assert "delegate_researcher" in names


@pytest.mark.asyncio
async def test_a_delegate_tool_offers_the_model_only_the_task():
    """The delegate_<name> schema came from the child's run() signature, so
    the model was offered tags, provenance and the private _resume, and
    filled tags and provenance in on its own (docs pass, 2026-09-27)."""
    child = OmniCoreAgent(name="researcher", system_instruction="Research.", model_config=MODEL, agent_config=CONFIG)
    lead = OmniCoreAgent(name="lead", system_instruction="Lead.", model_config=MODEL, sub_agents=[child], agent_config=CONFIG)
    (tool,) = [t for t in await lead.list_all_available_tools() if t["name"] == "delegate_researcher"]
    assert set(tool["inputSchema"]["properties"]) == {"query"}
    assert tool["inputSchema"]["required"] == ["query"]


@pytest.mark.asyncio
async def test_the_latest_trace_of_a_session_is_this_agents_own(tmp_path, monkeypatch):
    """After a delegation, get_latest_trace(session) returned the child's
    trace: the child shares the session (Build stranger test)."""
    monkeypatch.chdir(tmp_path)
    child = OmniCoreAgent(name="quizzer", system_instruction="Quiz.", model_config=MODEL, agent_config=CONFIG)
    await child.initialize()
    child.llm_connection = RecordingModel([], "three questions")
    lead = OmniCoreAgent(name="buddy", system_instruction="Lead.", model_config=MODEL, sub_agents=[child], agent_config=CONFIG)
    await lead.initialize()
    lead.llm_connection = RecordingModel([("d1", "delegate_quizzer", '{"query": "quiz"}')], "done")

    result = await lead.run("quiz me", session_id="study")
    latest = await lead.get_latest_trace("study")

    assert latest["trace_id"] == result["trace_id"]
