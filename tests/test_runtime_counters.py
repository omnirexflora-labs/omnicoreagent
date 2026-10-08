"""/prometheus says what runs, models, budgets and approvals did.

The support desk ramp (2026-10-07) could be explained from traces, one at a
time, but a person watching a server under load had only request counts: not
how many runs failed and why, how many waited for a person, how many were
resumed and by whom, or how many model calls the provider rejected.
"""

from __future__ import annotations

import json

import pytest

from omnicoreagent.core.metrics import COUNTERS
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from test_budget_enforcement import PricedModel, _agent as _budget_agent
from test_durable_runs_end_to_end import _agent as _approval_agent, _tools as _approval_tools
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel


def _read() -> dict[str, float]:
    out: dict[str, float] = {}
    for line in COUNTERS.prometheus_lines():
        if not line.startswith("#"):
            series, value = line.rsplit(" ", 1)
            out[series] = float(value)
    return out


class _Delta:
    """What the counters gained since this was made."""

    def __init__(self) -> None:
        self.before = _read()

    def __getitem__(self, series: str) -> float:
        return _read().get(series, 0) - self.before.get(series, 0)

    def new_series(self) -> dict[str, float]:
        now = _read()
        return {k: v - self.before.get(k, 0) for k, v in now.items() if v != self.before.get(k, 0)}


@pytest.mark.asyncio
async def test_an_approval_run_counts_its_ask_decision_wait_resume_and_finish(tmp_path):
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "sent",
    )
    agent = await _approval_agent(model, _approval_tools(tmp_path / "ledger", {"armed": False}))
    seen = _Delta()

    paused = await agent.run("go", session_id="session-m1", run_id="run_m1")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_m1", approval["approval_id"], decision="approve", approver="a")
    await agent.resume("run_m1")

    changed = seen.new_series()
    risk = approval["risk_level"]
    assert changed[f'omniserve_approvals_requested_total{{risk="{risk}"}}'] == 1
    assert seen['omniserve_approvals_decided_total{decision="approve"}'] == 1
    assert seen['omniserve_runs_finished_total{reason="none",status="awaiting_approval"}'] == 1
    assert seen['omniserve_runs_finished_total{reason="stop",status="completed"}'] == 1
    assert seen['omniserve_runs_resumed_total{cause="approval",trigger="explicit"}'] == 1
    assert seen['omniserve_model_calls_total{model="unknown"}'] == 2
    # No run id, session id or tool argument in any label.
    for series in changed:
        assert "run_m1" not in series and "session-m1" not in series and "INV-1" not in series


@pytest.mark.asyncio
async def test_a_refused_model_call_counts_as_a_model_error():
    class Rejects:
        def estimate_cost(self, usage):
            return None

        async def llm_call(self, messages, tools=None, **kwargs):
            raise PermissionError("AuthenticationError: bad key")

    agent = OmniCoreAgent(
        name="k",
        system_instruction="x",
        model_config=_MODEL,
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = Rejects()
    seen = _Delta()

    await agent.run("hi", session_id="err")

    assert seen['omniserve_model_calls_total{model="unknown"}'] == 1
    assert seen['omniserve_model_errors_total{model="unknown"}'] == 1
    failed = [k for k in seen.new_series() if k.startswith("omniserve_runs_finished_total{") and 'status="failed"' in k]
    assert failed and 'reason="provider_error"' in failed[0]
    await agent.cleanup()


@pytest.mark.asyncio
async def test_a_budget_that_stops_a_run_is_counted():
    asking = ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),))
    agent = await _budget_agent(
        PricedModel(asking), budgets={"request": [{"meter": "model_calls", "limit": 1}]}
    )
    seen = _Delta()

    result = await agent.run("go", session_id="bud")

    assert result["status"] == "awaiting_budget"
    assert seen['omniserve_budget_refused_total{action="pause",meter="model_calls",scope="request"}'] == 1
    assert seen['omniserve_runs_finished_total{reason="none",status="awaiting_budget"}'] == 1


@pytest.mark.asyncio
async def test_a_background_retry_is_counted_apart_from_an_explicit_resume(tmp_path):
    model = RecordingModel(
        [("t1", "draft", "{}"), ("t2", "send_invoice", json.dumps({"invoice": "INV-1"}))],
        "sent",
    )
    agent = await _approval_agent(model, _approval_tools(tmp_path / "ledger", {"armed": False}))
    paused = await agent.run("go", session_id="bg", run_id="run_bg")
    (approval,) = paused["approvals"]
    await agent.resolve_approval("run_bg", approval["approval_id"], decision="approve", approver="a")
    seen = _Delta()

    await agent.resume("run_bg", trigger="background_retry")

    assert seen['omniserve_runs_resumed_total{cause="approval",trigger="background_retry"}'] == 1
    assert seen['omniserve_runs_resumed_total{cause="approval",trigger="explicit"}'] == 0
