"""`omnicoreagent run`: one unattended run, from instruction to a readable result.

Nobody is there to answer an approval or top up a budget, so both are
decided by an explicit policy, through the same API a person uses, and
recorded on the run. The process exits with a code naming the terminal state
and leaves result.json and trajectory.json for the harness.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from click.testing import CliRunner

import omnicoreagent.cli as cli_module
from omnicoreagent.cli import cli
from omnicoreagent.cli.headless import (
    APPROVER,
    ApprovalPolicy,
    ApprovalPolicyError,
    ExitCode,
    HeadlessRequest,
    build_provenance,
    execute_headless,
    write_outputs,
)
from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy
from test_budget_enforcement import PricedModel
from test_budget_enforcement import _agent as _budget_agent
from test_execute_tool import _MODEL
from test_run_suspend import RecordingModel

SEND = [("t1", "send_invoice", json.dumps({"invoice": "INV-1"}))]


def _tools(ledger, *, slow: float = 0.0):
    tools = ToolRegistry()

    @tools.register_tool("send_invoice", description="Sends the invoice.")
    async def send_invoice(invoice: str) -> dict:
        if slow:
            await asyncio.sleep(slow)
        with ledger.open("a") as f:
            f.write(f"sent {invoice}\n")
        return {"status": "success", "data": {"sent": invoice}}

    return tools


def _policy(*, ask: bool):
    policy = build_default_policy("interactive-dev")
    if ask:
        policy.rules.ask.insert(
            0,
            PolicyRule(
                rule_id="ask_before_sending",
                effect=PolicyEffect.ASK,
                capability="tool.local.call",
                target={"tool_name": "send_invoice"},
            ),
        )
    return policy


async def _agent(model, ledger, *, ask=True, slow=0.0):
    agent = OmniCoreAgent(
        name="billing",
        system_instruction="Handle invoices.",
        model_config=_MODEL,
        local_tools=_tools(ledger, slow=slow),
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": False,
            "governance_config": {"enabled": True, "policy": _policy(ask=ask)},
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = model
    return agent


def _sent(ledger):
    return ledger.read_text().splitlines() if ledger.exists() else []


# --- terminal states ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_run_succeeds_with_its_trajectory_and_provenance(tmp_path):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(SEND, "sent"), ledger, ask=False)

    outcome = await execute_headless(
        agent,
        HeadlessRequest(
            instruction="send the invoice",
            provenance=build_provenance(["trial_id=trial-3", "adapter=harbor", "job=nightly"]),
        ),
    )

    assert (outcome.status, outcome.exit_code) == ("success", ExitCode.SUCCESS)
    assert outcome.response == "sent"
    assert _sent(ledger) == ["sent INV-1"]
    assert outcome.trajectory["status"] == "completed"
    assert outcome.trace_ids and outcome.evidence_error is None
    trace = await agent.telemetry_store.get_trace(outcome.trace_ids[0])
    assert trace.provenance.trial_id == "trial-3"
    assert trace.provenance.adapter == "harbor"
    assert trace.provenance.external_ids == {"job": "nightly"}
    assert {"headless", "approval-mode:stop", "budget-mode:stop"} <= set(trace.metadata.tags)


@pytest.mark.asyncio
async def test_stop_mode_leaves_the_run_waiting_and_exits_awaiting_approval(tmp_path):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(SEND, "sent"), ledger)

    outcome = await execute_headless(agent, HeadlessRequest(instruction="send it"))

    assert (outcome.status, outcome.exit_code) == ("awaiting_approval", ExitCode.AWAITING_APPROVAL)
    assert _sent(ledger) == [], "nothing unapproved ran"
    [pending] = outcome.pending["approvals"]
    assert pending["tool_name"] == "send_invoice"
    assert outcome.cli_decisions == []


@pytest.mark.asyncio
async def test_allow_mode_approves_as_the_cli_and_the_run_finishes(tmp_path):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(SEND, "sent"), ledger)

    outcome = await execute_headless(
        agent, HeadlessRequest(instruction="send it", approvals=ApprovalPolicy(mode="allow"))
    )

    assert (outcome.status, outcome.exit_code) == ("success", ExitCode.SUCCESS)
    assert _sent(ledger) == ["sent INV-1"]
    [decision] = outcome.cli_decisions
    assert (decision["decision"], decision["tool_name"]) == ("approve", "send_invoice")
    record = await agent.get_run(outcome.run_id)
    assert record["approvals"][0]["approver"] == APPROVER
    assert record["approvals"][0]["note"] == "approval-mode=allow"
    assert len(outcome.trace_ids) == 2, "the pause and the resume are both in the story"


@pytest.mark.asyncio
async def test_deny_mode_never_runs_the_call_and_the_model_hears_why(tmp_path):
    ledger = tmp_path / "ledger"
    model = RecordingModel(SEND, "could not send: denied")
    agent = await _agent(model, ledger)

    outcome = await execute_headless(
        agent, HeadlessRequest(instruction="send it", approvals=ApprovalPolicy(mode="deny"))
    )

    assert outcome.status == "success"
    assert _sent(ledger) == []
    assert outcome.cli_decisions[0]["decision"] == "deny"
    assert "denied by the headless run's approval policy" in json.dumps(model.calls[-1])


@pytest.mark.asyncio
async def test_scripted_rules_match_first_and_fall_back_to_the_default(tmp_path):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(SEND, "sent"), ledger)
    policy = ApprovalPolicy(
        mode="scripted",
        rules=[{"tool_name": "send_invoice", "decision": "approve", "note": "case allows sending"}],
    )

    outcome = await execute_headless(agent, HeadlessRequest(instruction="send", approvals=policy))

    assert outcome.status == "success" and _sent(ledger) == ["sent INV-1"]
    assert outcome.cli_decisions[0]["rule"] == 0
    assert (await agent.get_run(outcome.run_id))["approvals"][0]["note"] == "case allows sending"


@pytest.mark.asyncio
async def test_scripted_default_stop_leaves_unmatched_requests_waiting(tmp_path):
    ledger = tmp_path / "ledger"
    agent = await _agent(RecordingModel(SEND, "sent"), ledger)
    policy = ApprovalPolicy(
        mode="scripted", rules=[{"tool_name": "other", "decision": "approve"}], default="stop"
    )

    outcome = await execute_headless(agent, HeadlessRequest(instruction="send", approvals=policy))

    assert outcome.exit_code == ExitCode.AWAITING_APPROVAL
    assert _sent(ledger) == []


def _two_turns():
    return PricedModel(ModelTurn(tool_calls=(ToolRequest("call_1", "lookup", '{"key": "a"}'),)))


ONE_CALL = {"request": [{"meter": "model_calls", "limit": 1}]}


@pytest.mark.asyncio
async def test_budget_stop_exits_awaiting_budget_with_what_it_needs():
    agent = await _budget_agent(_two_turns(), budgets=ONE_CALL)

    outcome = await execute_headless(agent, HeadlessRequest(instruction="go"))

    assert (outcome.status, outcome.exit_code) == ("awaiting_budget", ExitCode.AWAITING_BUDGET)
    assert outcome.pending["budget_request"]["meter"] == "model_calls"


@pytest.mark.asyncio
async def test_budget_deny_ends_the_run_instead_of_waiting():
    agent = await _budget_agent(_two_turns(), budgets=ONE_CALL)

    outcome = await execute_headless(agent, HeadlessRequest(instruction="go", budget_mode="deny"))

    assert outcome.status not in {"awaiting_budget", "timeout"}
    [decision] = outcome.cli_decisions
    assert (decision["kind"], decision["decision"], decision["meter"]) == ("budget", "deny", "model_calls")
    record = await agent.get_run(outcome.run_id)
    assert record["status"] != "awaiting_budget"


@pytest.mark.asyncio
async def test_the_deadline_covers_the_run_and_is_recorded_as_a_timeout(tmp_path):
    ledger = tmp_path / "ledger"
    # The tool takes far longer than the deadline, and the deadline is long
    # enough to land after the run has started recording: a deadline that fires
    # during startup leaves no trace to read, which is a loaded machine's
    # timing rather than what this test is about.
    agent = await _agent(RecordingModel(SEND, "sent"), ledger, ask=False, slow=30.0)

    outcome = await execute_headless(agent, HeadlessRequest(instruction="send", timeout=2.0))

    assert (outcome.status, outcome.exit_code) == ("timeout", ExitCode.TIMEOUT)
    assert outcome.trace_ids, "a run that timed out recorded no trace"
    trace = await agent.telemetry_store.get_trace(outcome.trace_ids[0])
    assert trace.status.value == "timeout"


@pytest.mark.asyncio
async def test_an_agent_that_raises_is_a_failed_run_not_a_crash(tmp_path):
    class Broken:
        async def llm_call(self, *args, **kwargs):
            raise RuntimeError("provider down")

    agent = await _agent(Broken(), tmp_path / "ledger", ask=False)

    outcome = await execute_headless(agent, HeadlessRequest(instruction="send"))

    assert outcome.exit_code == ExitCode.FAILED
    assert outcome.status in {"error", "failed"}


# --- policy and provenance parsing ----------------------------------------------


def test_approvals_file_is_validated(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"default": "stop", "rules": [{"capability": "sandbox.execute", "decision": "approve"}]}))
    policy = ApprovalPolicy.from_file(good)
    assert policy.decide({"capability": "sandbox.execute"}).decision == "approve"
    assert policy.decide({"capability": "network"}).decision is None

    for bad in (
        {"rules": [{"decision": "approve"}]},
        {"rules": [{"tool_name": "x", "decision": "maybe"}]},
        {"rules": [{"tool_name": "x", "decision": "deny", "arguments": {}}]},
        {"default": "sometimes"},
        {"unexpected": 1},
    ):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ApprovalPolicyError):
            ApprovalPolicy.from_file(path)


def test_provenance_pairs_split_known_fields_from_external_ids():
    assert build_provenance([]) is None
    assert build_provenance(["case_id=c1", "ticket=T-1"]) == {
        "case_id": "c1",
        "external_ids": {"ticket": "T-1"},
    }
    with pytest.raises(ValueError):
        build_provenance(["no-equals"])


# --- the command ----------------------------------------------------------------


def test_the_command_writes_results_and_exits_with_the_terminal_state(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger"
    agent = asyncio.run(_agent(RecordingModel(SEND, "sent"), ledger))
    monkeypatch.setattr(cli_module, "load_agent", lambda path: agent)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        ["run", "--agent", "agent.py", "-i", "send it", "--approval-mode", "allow",
         "--provenance", "trial_id=t-9", "-o", str(out)],
    )

    assert result.exit_code == 0, result.output
    document = json.loads((out / "result.json").read_text())
    assert document["schema"] == "omnicoreagent.headless_result/v1"
    assert (document["status"], document["exit_code"], document["approval_mode"]) == ("success", 0, "allow")
    assert json.loads((out / "trajectory.json").read_text())["run_id"] == document["run_id"]


def test_the_command_exit_code_names_a_waiting_run(tmp_path, monkeypatch):
    agent = asyncio.run(_agent(RecordingModel(SEND, "sent"), tmp_path / "ledger"))
    monkeypatch.setattr(cli_module, "load_agent", lambda path: agent)

    result = CliRunner().invoke(cli, ["run", "--agent", "agent.py", "-i", "send it", "--json"])

    assert result.exit_code == ExitCode.AWAITING_APPROVAL
    assert '"status": "awaiting_approval"' in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["run", "--agent", "a.py"],
        ["run", "--agent", "a.py", "-i", "x", "-f", "file.txt"],
        ["run", "--agent", "a.py", "-i", "   "],
        ["run", "--agent", "a.py", "-i", "x", "--approval-mode", "scripted"],
        ["run", "--agent", "a.py", "-i", "x", "--approvals-file", "rules.json"],
        ["run", "--agent", "a.py", "-i", "x", "--provenance", "oops"],
    ],
)
def test_bad_invocations_are_usage_errors(args):
    assert CliRunner().invoke(cli, args).exit_code == ExitCode.USAGE


def test_a_missing_agent_file_is_a_startup_error(tmp_path):
    result = CliRunner().invoke(cli, ["run", "--agent", str(tmp_path / "nope.py"), "-i", "x"])
    assert result.exit_code == ExitCode.USAGE
    assert "Agent file not found" in result.output


def test_an_agent_file_without_an_agent_is_a_startup_error(tmp_path):
    path = tmp_path / "empty_agent.py"
    path.write_text("x = 1\n")
    result = CliRunner().invoke(cli, ["run", "--agent", str(path), "-i", "x"])
    assert result.exit_code == ExitCode.USAGE
    assert "must define an 'agent'" in result.output


def test_outputs_are_written_even_without_a_trajectory(tmp_path):
    from omnicoreagent.cli.headless import HeadlessOutcome

    written = write_outputs(HeadlessOutcome(status="error", exit_code=1, run_id="r", session_id=None), tmp_path)
    assert [p.name for p in written] == ["result.json"]
