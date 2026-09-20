"""End-to-end acceptance for governed execution.

The governed execution plan (engineering/architecture/governed-execution-plan.md)
says an agent may run code in three lanes — its own tools, commands in a
sandbox (``execute`` and skill scripts), and a program in code mode — and that
every one of them is authorized by policy, contained where it should be, and
readable afterwards in the run's trace and record. This script runs one
scenario that uses all three, pauses for a person's approval, resumes, and
then checks eight items from what the run left behind.

Modes:

    python engineering/validation/execution_acceptance.py --check-fixture
        Standard library only. Checks the committed, sanitized records without
        importing OmniCoreAgent, Docker, or Monty.

    PYTHONPATH=src .venv/bin/python engineering/validation/execution_acceptance.py --run
        Runs the scenario for real: a Docker sandbox and a Monty program, in a
        temporary workspace.

    PYTHONPATH=src .venv/bin/python engineering/validation/execution_acceptance.py --write-fixtures
        Runs the scenario and rewrites the committed fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "execution-acceptance.json"
_IDENTIFIER = re.compile(r"((?:trace|run|span|event|sandbox|approval|owner|exec|decision|authreq)_)[0-9a-f]{16,}")

CHECKLIST = (
    "1. the run reads as one story: request, both segments, final answer",
    "2. the sandbox session is recorded, opened and closed, with its provider",
    "3. every sandboxed command is recorded with its exit code and the rule that allowed it",
    "4. a file the sandbox wrote came back as a governed workspace write",
    "5. a program's tool calls are nested under run_code, each with its own decision",
    "6. the run paused for approval and nothing unapproved ran",
    "7. the run record shows every call's state and both trace segments",
    "8. run totals count the executions, and nothing was lost from the record",
)


# --- the scenario ------------------------------------------------------------


def _model(workspace: Path):
    """A scripted model that uses all three execution lanes, then pauses."""
    from omnicoreagent.core.model_protocol import ModelTurn, ToolRequest
    from omnicoreagent.core.token_usage import Usage

    turns = [
        [
            ("w1", "write_file", json.dumps({"path": "numbers.txt", "content": "3\n4\n5"})),
            ("w2", "write_file", json.dumps({"path": "old.txt", "content": "stale"})),
        ],
        [("e1", "execute", json.dumps({"command": "awk '{s+=$1} END {print s}' numbers.txt > total.txt"}))],
        [("k1", "run_code", json.dumps({"code": "total = int(read_file(path='total.txt')['content'])\nprint('doubled', total * 2)\ntotal * 2"}))],
        [("d1", "delete_file", json.dumps({"path": "old.txt"}))],
        "The numbers add up to 12, and twice that is 24. The stale file is gone.",
    ]

    class ScriptedModel:
        def __init__(self):
            self.turns = list(turns)

        def estimate_cost(self, usage):
            return None

        async def llm_call(self, messages, tools=None, **kwargs):
            usage = Usage(requests=1, request_tokens=20, response_tokens=5, total_tokens=25)
            turn = self.turns.pop(0)
            if isinstance(turn, str):
                return ModelTurn(content=turn, finish_reason="stop", usage=usage)
            return ModelTurn(
                tool_calls=tuple(ToolRequest(*call) for call in turn),
                finish_reason="tool_calls",
                usage=usage,
            )

    return ScriptedModel()


def _policy():
    from omnicoreagent.governance import PolicyEffect, PolicyRule, build_default_policy

    policy = build_default_policy("interactive-dev")
    policy.rules.ask.insert(
        0,
        PolicyRule(
            rule_id="ask_before_deleting",
            effect=PolicyEffect.ASK,
            capability="workspace.files.delete",
        ),
    )
    return policy


async def run_scenario(workspace: Path) -> dict[str, Any]:
    """Run the scenario and return what it left behind."""
    from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

    agent = OmniCoreAgent(
        name="acceptance",
        system_instruction="Add up the numbers, then tidy up.",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "unused"},
        agent_config={
            "guardrail_mode": "off",
            "enable_workspace_files": True,
            "workspace_config": {"workspace_dir": str(workspace)},
            "code_mode": {"enabled": True, "tools": ["read_file"], "snapshot_key": "acceptance"},
            "governance_config": {
                "enabled": True,
                "policy": _policy(),
                "sandbox_config": {"provider": "docker", "options": {"image": "alpine:3.20"}},
            },
        },
        telemetry_config={"capture": "full"},
    )
    await agent.initialize()
    agent.llm_connection = _model(workspace)

    paused = await agent.run("add up the numbers and tidy up", session_id="acceptance")
    approvals = paused.get("approvals") or []
    if paused.get("status") != "awaiting_approval" or not approvals:
        raise SystemExit(f"the run did not pause for approval: {paused.get('status')}")
    stale_before_decision = (workspace / "files" / "old.txt").exists()
    await agent.resolve_approval(
        paused["run_id"], approvals[0]["approval_id"], decision="approve", approver="reviewer"
    )
    finished = await agent.resume(paused["run_id"])

    record = await agent.get_run(paused["run_id"])
    events = []
    for trace_id in record.get("trace_ids") or []:
        trace = await agent.telemetry_store.get_trace(trace_id)
        events.extend(
            {"trace_id": trace_id, "event_type": event.event_type, "metadata": dict(event.metadata)}
            for event in trace.events
        )
    return {
        "events": events,
        "paused": {"status": paused["status"], "approvals": approvals},
        "stale_file_survived_until_approval": stale_before_decision,
        "final": {"status": finished.get("status"), "response": finished.get("response")},
        "record": record,
        "story": await agent.get_run_trajectory(paused["run_id"]),
        "workspace_files": sorted(p.name for p in (workspace / "files").glob("*")),
    }


# --- the checks --------------------------------------------------------------


def check(result: dict[str, Any]) -> list[str]:
    """Check every checklist item; returns the failures."""
    failures: list[str] = []
    story = result["story"]
    record = result["record"]
    segments = story.get("segments") or []
    calls = [
        call
        for segment in segments
        for step in (segment.get("trajectory") or {}).get("steps") or []
        for call in step["tool_calls"]
    ]
    events = result.get("events") or []

    def fails(number: int, why: str) -> None:
        failures.append(f"{CHECKLIST[number - 1]}: {why}")

    # 1. one story
    first = (segments[0].get("trajectory") or {}) if segments else {}
    if len(segments) != 2:
        fails(1, f"expected two segments, found {len(segments)}")
    if (first.get("request") or {}).get("message") != "add up the numbers and tidy up":
        fails(1, "the request is not in the first segment")
    if "12" not in (result["final"].get("response") or ""):
        fails(1, "the final answer is missing")

    # 2. the sandbox session
    sessions = [e for e in events if e["event_type"] == "sandbox_session_created"]
    closed = [e for e in events if e["event_type"] == "sandbox_session_closed"]
    if not sessions or not closed:
        fails(2, f"created={len(sessions)} closed={len(closed)}")
    elif sessions[0]["metadata"].get("sandbox_provider") != "docker":
        fails(2, f"provider was {sessions[0]['metadata'].get('sandbox_provider')}")

    # 3. commands and their decisions
    commands = [
        e
        for e in events
        if e["event_type"] in {"sandbox_exec_completed", "sandbox_exec_failed"}
        and e["metadata"].get("purpose") != "workspace_sync"
    ]
    if not commands:
        fails(3, "no sandboxed command was recorded")
    for command in commands:
        if command["metadata"].get("matched_rule_ids") != ["allow_sandboxed_execution"]:
            fails(3, f"unexpected rule: {command['metadata'].get('matched_rule_ids')}")
        if command["metadata"].get("exit_code") != 0:
            fails(3, f"exit code {command['metadata'].get('exit_code')}")

    # 4. the workspace bridge
    synced = [e for e in events if e["event_type"] == "sandbox_workspace_sync"]
    written = {path for e in synced for path in e["metadata"].get("written") or []}
    if "total.txt" not in written:
        fails(4, f"the sandbox's file did not come back: {sorted(written)}")
    if "total.txt" not in result["workspace_files"]:
        fails(4, "total.txt is not in the workspace")
    sandbox_changes = [
        change
        for segment in segments
        for change in ((segment.get("trajectory") or {}).get("totals") or {}).get("workspace_changes") or []
        if change.get("via") == "sandbox"
    ]
    if not sandbox_changes:
        fails(4, "the run totals do not report a workspace change from the sandbox")

    # 5. code mode
    programs = [call for call in calls if call["tool_name"] == "run_code"]
    if not programs:
        fails(5, "the program did not run")
    for program in programs:
        inner = program.get("code_calls") or []
        if not inner:
            fails(5, "the program's tool calls are not nested under it")
        for call in inner:
            if call["outcome"] != "success":
                fails(5, f"{call['tool_name']} ended {call['outcome']}")
            if not call.get("governance"):
                fails(5, f"{call['tool_name']} has no policy decision")

    # 6. the approval
    approvals = record.get("approvals") or []
    if not approvals or approvals[0].get("approver") != "reviewer":
        fails(6, f"approvals: {approvals}")
    if not result["stale_file_survived_until_approval"]:
        fails(6, "the file was deleted before anyone approved it")
    if "old.txt" in result["workspace_files"]:
        fails(6, "the approved delete did not run after approval")

    # 7. the run record
    states = {call["tool_call_id"]: call["state"] for call in record.get("tool_calls") or []}
    if states.get("d1") != "completed":
        fails(7, f"the approved call is {states.get('d1')}")
    if len(record.get("trace_ids") or []) != 2:
        fails(7, f"trace segments: {record.get('trace_ids')}")
    if record.get("status") != "completed":
        fails(7, f"the run is {record.get('status')}")

    # 8. totals and capture gaps
    executions = (story.get("totals") or {}).get("executions") or {}
    if not executions.get("commands"):
        fails(8, f"executions: {executions}")
    # Under governance, arguments are recorded redacted on purpose; the reader
    # reports that honestly. What must not appear is a payload that was lost.
    lost = [
        gap
        for segment in segments
        for gap in (segment.get("trajectory") or {}).get("capture_gaps") or []
        if gap.get("state") not in {"redacted", "not_recorded"}
    ]
    if lost:
        fails(8, f"payloads missing from the record: {lost}")
    return failures


def report(result: dict[str, Any], *, label: str) -> None:
    failures = check(result)
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        raise SystemExit(f"{label}: {len(failures)} of {len(CHECKLIST)} checklist items failed")
    print(f"{label}: {len(CHECKLIST)} checklist items passed")


# --- modes -------------------------------------------------------------------


def run_scripted() -> tuple[dict[str, Any], str]:
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / "workspace"
        result = asyncio.run(run_scenario(workspace))
    report(result, label="execution acceptance")
    return result, str(workspace)


def check_fixture() -> None:
    if not FIXTURE.exists():
        raise SystemExit(f"no fixture at {FIXTURE}; run --write-fixtures")
    report(json.loads(FIXTURE.read_text()), label="execution acceptance (fixture)")


def write_fixtures() -> None:
    result, workspace = run_scripted()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(_sanitized(result, workspace=workspace), indent=2, sort_keys=True, default=str)
        + "\n"
    )
    print(f"wrote {FIXTURE}")


def _sanitized(value: Any, *, workspace: str | None = None) -> Any:
    """Identifiers and timestamps vary per run; the shape is what matters."""
    if isinstance(value, dict):
        return {key: _sanitized(item, workspace=workspace) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitized(item, workspace=workspace) for item in value]
    if isinstance(value, str):
        if workspace and workspace in value:
            # The run used a temporary workspace; its path is not part of the proof.
            value = value.replace(workspace, "<workspace>")
        # Only identifiers (a prefix and a hex string), never names such as
        # the event type "sandbox_workspace_sync".
        match = _IDENTIFIER.fullmatch(value)
        if match:
            return f"{match.group(1)}<id>"
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check-fixture", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--write-fixtures", action="store_true")
    arguments = parser.parse_args()
    if arguments.check_fixture:
        check_fixture()
    elif arguments.write_fixtures:
        write_fixtures()
    elif arguments.run:
        run_scripted()
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
