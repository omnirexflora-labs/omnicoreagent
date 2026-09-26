"""One unattended run, from instruction to a result a harness can read.

A headless run has nobody to answer an approval or top up a budget, so both
are decided by an explicit, recorded policy instead of leaving the run
waiting forever. Every decision the CLI makes is resolved through the same
public API a person would use, with the approver ``omnicoreagent-cli`` and a
note naming the mode, so the run's own record says who decided and how.

The outcome is written as ``result.json`` (status, exit code, identifiers,
the answer, the CLI's decisions) and ``trajectory.json`` (the run's durable
trajectory across every pause and resume), and the process exits with a code
that names the terminal state.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field, fields
from enum import IntEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from omnicoreagent.core.runtime.deadline import run_with_timeout
from omnicoreagent.core.telemetry.context import entry_surface
from omnicoreagent.core.telemetry.models import TelemetryProvenance

APPROVER = "omnicoreagent-cli"
RESULT_SCHEMA = "omnicoreagent.headless_result/v1"
APPROVAL_MODES = ("stop", "allow", "deny", "scripted")
BUDGET_MODES = ("stop", "deny")
_DECISIONS = ("approve", "deny")


class ExitCode(IntEnum):
    SUCCESS = 0
    FAILED = 1
    USAGE = 2
    AWAITING_APPROVAL = 3
    AWAITING_BUDGET = 4
    TIMEOUT = 5
    INTERRUPTED = 6


_EXIT_BY_STATUS = {
    "success": ExitCode.SUCCESS,
    "awaiting_approval": ExitCode.AWAITING_APPROVAL,
    "awaiting_budget": ExitCode.AWAITING_BUDGET,
    "timeout": ExitCode.TIMEOUT,
    "interrupted": ExitCode.INTERRUPTED,
}


def exit_code_for(status: str) -> ExitCode:
    return _EXIT_BY_STATUS.get(status, ExitCode.FAILED)


class ApprovalPolicyError(ValueError):
    """An approvals file or approval mode is invalid."""


@dataclass(frozen=True)
class ApprovalDecision:
    decision: str | None  # "approve", "deny", or None to leave it waiting
    note: str
    rule: int | None = None


@dataclass
class ApprovalPolicy:
    """How a headless run answers the approvals its policy asks for.

    ``stop`` leaves the run waiting and exits; ``allow`` approves and ``deny``
    denies every request; ``scripted`` matches each request against ordered
    rules (``tool_name`` and/or ``capability``), first match wins, and falls
    back to ``default`` (``approve``, ``deny``, or ``stop``).
    """

    mode: str = "stop"
    rules: list[dict[str, Any]] = field(default_factory=list)
    default: str = "deny"

    def __post_init__(self) -> None:
        if self.mode not in APPROVAL_MODES:
            raise ApprovalPolicyError(
                f"approval mode must be one of {', '.join(APPROVAL_MODES)}"
            )
        if self.default not in (*_DECISIONS, "stop"):
            raise ApprovalPolicyError("approvals default must be approve, deny, or stop")
        for index, rule in enumerate(self.rules):
            if not isinstance(rule, dict):
                raise ApprovalPolicyError(f"approval rule {index} must be an object")
            if rule.get("decision") not in _DECISIONS:
                raise ApprovalPolicyError(
                    f"approval rule {index} needs a decision of approve or deny"
                )
            if not (rule.get("tool_name") or rule.get("capability")):
                raise ApprovalPolicyError(
                    f"approval rule {index} must match on tool_name or capability"
                )
            unknown = set(rule) - {"tool_name", "capability", "decision", "note"}
            if unknown:
                raise ApprovalPolicyError(
                    f"approval rule {index} has unknown keys: {', '.join(sorted(unknown))}"
                )

    @classmethod
    def from_file(cls, path: str | Path) -> ApprovalPolicy:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApprovalPolicyError(f"cannot read approvals file {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ApprovalPolicyError("approvals file must be a JSON object")
        unknown = set(document) - {"default", "rules"}
        if unknown:
            raise ApprovalPolicyError(
                f"approvals file has unknown keys: {', '.join(sorted(unknown))}"
            )
        rules = document.get("rules") or []
        if not isinstance(rules, list):
            raise ApprovalPolicyError("approvals file 'rules' must be a list")
        return cls(mode="scripted", rules=rules, default=document.get("default", "deny"))

    def decide(self, approval: dict[str, Any]) -> ApprovalDecision:
        if self.mode == "stop":
            return ApprovalDecision(None, "approval-mode=stop")
        if self.mode == "allow":
            return ApprovalDecision("approve", "approval-mode=allow")
        if self.mode == "deny":
            return ApprovalDecision(
                "deny", "approval-mode=deny: denied by the headless run's approval policy"
            )
        for index, rule in enumerate(self.rules):
            if rule.get("tool_name") and rule["tool_name"] != approval.get("tool_name"):
                continue
            if rule.get("capability") and rule["capability"] != approval.get("capability"):
                continue
            note = rule.get("note") or f"approval-mode=scripted: rule {index}"
            return ApprovalDecision(rule["decision"], note, rule=index)
        if self.default == "stop":
            return ApprovalDecision(None, "approval-mode=scripted: no rule matched")
        return ApprovalDecision(self.default, "approval-mode=scripted: default")


_PROVENANCE_FIELDS = {
    f.name for f in fields(TelemetryProvenance) if f.name not in {"external_ids", "extra"}
}


def build_provenance(pairs: list[str]) -> dict[str, Any] | None:
    """``key=value`` pairs: known provenance fields are set directly, any
    other key is kept under ``external_ids``."""
    if not pairs:
        return None
    provenance: dict[str, Any] = {}
    external: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"provenance must be key=value, got {pair!r}")
        if key in _PROVENANCE_FIELDS:
            provenance[key] = value
        else:
            external[key] = value
    if external:
        provenance["external_ids"] = external
    return provenance


@dataclass
class HeadlessRequest:
    instruction: str
    session_id: str | None = None
    run_id: str | None = None
    tags: list[str] = field(default_factory=list)
    provenance: dict[str, Any] | None = None
    approvals: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    budget_mode: str = "stop"
    timeout: float | None = None
    max_approval_rounds: int = 20

    def __post_init__(self) -> None:
        if self.budget_mode not in BUDGET_MODES:
            raise ValueError(f"budget mode must be one of {', '.join(BUDGET_MODES)}")
        if self.max_approval_rounds < 1:
            raise ValueError("max approval rounds must be at least 1")


@dataclass
class HeadlessOutcome:
    status: str
    exit_code: int
    run_id: str
    session_id: str | None
    response: Any = None
    termination_reason: str | None = None
    error: str | None = None
    trace_ids: list[str] = field(default_factory=list)
    approval_mode: str = "stop"
    budget_mode: str = "stop"
    cli_decisions: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None
    usage: Any = None
    duration_seconds: float = 0.0
    omnicoreagent_version: str = ""
    evidence_error: str | None = None
    trajectory: dict[str, Any] | None = field(default=None, repr=False)

    def result_document(self) -> dict[str, Any]:
        document = asdict(self)
        document.pop("trajectory")
        return {"schema": RESULT_SCHEMA, **document}


def _package_version() -> str:
    try:
        return version("omnicoreagent")
    except PackageNotFoundError:
        return "0+unknown"


def _status_of(result: dict[str, Any]) -> str:
    status = result.get("status") or "success"
    if status == "error" and result.get("guardrail_result") is not None:
        return "blocked"
    return str(status)


async def execute_headless(agent: Any, request: HeadlessRequest) -> HeadlessOutcome:
    """Run one instruction to a terminal state, answering pauses by policy.

    The deadline covers the whole run, pauses and resumes included. Telemetry
    and evidence collection never change the outcome: a failure to read the
    trajectory is reported as ``evidence_error``. Every segment of the run is
    recorded as entered ``headless``.
    """
    with entry_surface("headless"):
        return await _execute_headless(agent, request)


async def _execute_headless(agent: Any, request: HeadlessRequest) -> HeadlessOutcome:
    started = time.monotonic()
    deadline = started + request.timeout if request.timeout and request.timeout > 0 else None
    run_id = request.run_id or agent.generate_run_id()
    tags = [
        *request.tags,
        "headless",
        f"approval-mode:{request.approvals.mode}",
        f"budget-mode:{request.budget_mode}",
    ]
    outcome = HeadlessOutcome(
        status="error",
        exit_code=ExitCode.FAILED,
        run_id=run_id,
        session_id=request.session_id,
        approval_mode=request.approvals.mode,
        budget_mode=request.budget_mode,
        omnicoreagent_version=_package_version(),
    )

    async def bounded(awaitable):
        if deadline is None:
            return await awaitable
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            awaitable.close()
            raise asyncio.TimeoutError("run exceeded its deadline")
        return await run_with_timeout(awaitable, remaining)

    async def start():
        if getattr(agent, "mcp_tools", None):
            await agent.connect_mcp_servers()
        return await agent.run(
            request.instruction,
            session_id=request.session_id,
            run_id=run_id,
            tags=tags,
            provenance=request.provenance,
        )

    result: dict[str, Any] = {}
    try:
        result = await bounded(start())
        rounds = 0
        budget_denied = False
        while True:
            status = _status_of(result)
            if status == "awaiting_approval" and request.approvals.mode != "stop":
                if rounds >= request.max_approval_rounds:
                    break
                rounds += 1
                undecided = False
                for approval in result.get("approvals") or []:
                    decision = request.approvals.decide(approval)
                    if decision.decision is None:
                        undecided = True
                        continue
                    await agent.resolve_approval(
                        run_id,
                        approval["approval_id"],
                        decision=decision.decision,
                        approver=APPROVER,
                        note=decision.note,
                    )
                    outcome.cli_decisions.append(
                        {
                            "kind": "approval",
                            "approval_id": approval["approval_id"],
                            "tool_name": approval.get("tool_name"),
                            "capability": approval.get("capability"),
                            "decision": decision.decision,
                            "rule": decision.rule,
                            "note": decision.note,
                        }
                    )
                if undecided:
                    break
                result = await bounded(agent.resume(run_id))
                continue
            if status == "awaiting_budget" and request.budget_mode == "deny" and not budget_denied:
                budget_denied = True
                budget_request = result.get("budget_request") or {}
                note = "budget-mode=deny: no top-up in a headless run"
                await agent.deny_budget(
                    run_id,
                    approver=APPROVER,
                    note=note,
                    request_id=budget_request.get("request_id"),
                )
                outcome.cli_decisions.append(
                    {
                        "kind": "budget",
                        "request_id": budget_request.get("request_id"),
                        "meter": budget_request.get("meter"),
                        "scope": budget_request.get("scope"),
                        "decision": "deny",
                        "note": note,
                    }
                )
                result = await bounded(agent.resume(run_id))
                continue
            break
        outcome.status = _status_of(result)
    except asyncio.TimeoutError:
        outcome.status = "timeout"
        outcome.error = "run exceeded its deadline"
    except Exception as exc:
        outcome.status = "error"
        outcome.error = f"{exc.__class__.__name__}: {exc}"

    outcome.exit_code = int(exit_code_for(outcome.status))
    outcome.session_id = result.get("session_id") or outcome.session_id
    outcome.response = result.get("response")
    outcome.termination_reason = result.get("termination_reason")
    outcome.usage = result.get("metric")
    if outcome.status == "awaiting_approval":
        outcome.pending = {"approvals": result.get("approvals") or []}
    elif outcome.status == "awaiting_budget":
        outcome.pending = {"budget_request": result.get("budget_request")}

    try:
        trajectory = await agent.get_run_trajectory(run_id)
        if trajectory is not None:
            outcome.trace_ids = [s["trace_id"] for s in trajectory.get("segments") or []]
        else:
            trajectory = await agent.get_trajectory(run_id=run_id)
            if trajectory is not None and trajectory.get("trace_id"):
                outcome.trace_ids = [trajectory["trace_id"]]
        outcome.trajectory = trajectory
    except Exception as exc:
        outcome.evidence_error = f"{exc.__class__.__name__}: {exc}"
    if not outcome.trace_ids and result.get("trace_id"):
        outcome.trace_ids = [result["trace_id"]]

    outcome.duration_seconds = round(time.monotonic() - started, 3)
    return outcome


def write_outputs(outcome: HeadlessOutcome, output_dir: str | Path) -> list[Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    result_path = directory / "result.json"
    result_path.write_text(
        json.dumps(outcome.result_document(), indent=2, default=str), encoding="utf-8"
    )
    written.append(result_path)
    if outcome.trajectory is not None:
        trajectory_path = directory / "trajectory.json"
        trajectory_path.write_text(
            json.dumps(outcome.trajectory, indent=2, default=str), encoding="utf-8"
        )
        written.append(trajectory_path)
    return written
