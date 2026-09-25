"""A final answer can be reviewed before it is accepted.

A real Harbor trial stopped at step 13 of 60 and reported that invalid input was
handled after checking a single invalid input; the verifier's eight other invalid
inputs all failed. With ``completion_review`` on, the
runtime asks once, when the model says it is done, for each requirement and the
check that showed it — and the run goes on in the same trace, where the request
is a recorded runtime message.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.agents.review import COMPLETION_REVIEW_PROMPT
from omnicoreagent.core.runtime.config import AgentConfig
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_credential_scrubbing import RecordingModel


async def _run(model, **agent_config):
    agent = OmniCoreAgent(
        name="reviewed",
        system_instruction="Do the task.",
        model_config={"provider": "openai", "model": "gpt-5.6-terra", "api_key": "k"},
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False, **agent_config},
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    try:
        result = await agent.run("build the thing", session_id="review")
        trajectory = await agent.get_trajectory(result["trace_id"])
    finally:
        await agent.cleanup()
    return result, trajectory


def _review_count(trajectory) -> int:
    """Runtime messages sit under the step they happened in, or at the top."""
    entries = list(trajectory.get("runtime_messages") or [])
    for step in trajectory.get("steps") or []:
        entries += step.get("runtime_messages") or []
    return sum(1 for entry in entries if entry.get("kind") == "completion_review")


def _last_user_message(request) -> str:
    users = [m for m in request if (m.get("role") if isinstance(m, dict) else None) == "user"]
    return json.dumps(users[-1]) if users else json.dumps(request[-1])


@pytest.mark.asyncio
async def test_without_it_the_first_final_answer_ends_the_run():
    model = RecordingModel("done")

    result, _ = await _run(model)

    assert result["response"] == "done"
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_a_final_answer_is_reviewed_once_and_the_run_goes_on():
    model = RecordingModel("done", "reviewed: each requirement checked")

    result, trajectory = await _run(model, completion_review=1)

    assert result["response"] == "reviewed: each requirement checked"
    assert len(model.requests) == 2
    second = json.dumps(model.requests[1])
    # The model's first answer is kept, and the review follows it.
    assert "done" in second
    assert "Before this is final" in _last_user_message(model.requests[1])
    # One run, one trace, and the review is on the record as the harness's.
    assert _review_count(trajectory) == 1


@pytest.mark.asyncio
async def test_the_review_lets_the_model_go_back_to_work():
    model = RecordingModel(
        "done",
        [("c1", "list_artifacts", "{}")],
        "now it is done",
    )

    result, _ = await _run(model, completion_review=1)

    assert result["response"] == "now it is done"
    assert len(model.requests) == 3, "a tool call after the review, then the answer"


@pytest.mark.asyncio
async def test_each_round_is_one_review_and_no_more():
    model = RecordingModel("one", "two", "three", "never asked")

    result, trajectory = await _run(model, completion_review=2)

    assert result["response"] == "three"
    assert _review_count(trajectory) == 2


def test_the_prompt_asks_for_evidence_not_reassurance():
    assert "check" in COMPLETION_REVIEW_PROMPT
    assert "did not run" in COMPLETION_REVIEW_PROMPT


@pytest.mark.parametrize("rounds", [-1, 4])
def test_rounds_are_bounded(rounds):
    with pytest.raises(ValueError, match="completion_review"):
        AgentConfig(agent_name="a", completion_review=rounds)


def test_it_is_off_by_default():
    assert AgentConfig(agent_name="a").completion_review == 0
