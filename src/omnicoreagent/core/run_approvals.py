"""Approvals a person decides after the run has asked.

When governance asks for approval and nothing answers in the moment, the
run's resolver records a pending approval on the run's record and returns no
answer, so governance refuses the call for now. A person decides it later with
``OmniCoreAgent.resolve_approval``. When the same request is authorized again
(on resume), the resolver returns that decision, once.

A decision is bound to the request's digest: capability, target, tool, server,
and a digest of the arguments. The execution surface is left out on purpose:
whether a skill script runs in a sandbox depends on the run's environment,
not on what was approved.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from omnicoreagent.core.metrics import COUNTERS
from omnicoreagent.core.runs import RunStateConflict, current_run
from omnicoreagent.governance.models import ApprovalRequest, ApprovalResult, to_plain

# How long an approval can wait for a decision when the policy sets no expiry.
DEFAULT_APPROVAL_TTL = timedelta(hours=24)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_digest(request: Any) -> str:
    """Digest of what an approval authorizes (an ApprovalRequest or AuthorityRequest)."""
    metadata = getattr(request, "metadata", None) or {}
    target = getattr(request, "target", None)
    payload = {
        "capability": request.capability,
        "actor": getattr(request, "actor", None),
        "target": to_plain(target) if target is not None else None,
        "provider": getattr(request, "provider", None),
        "method": getattr(request, "method", None),
        "host": getattr(request, "host", None),
        "mcp_server": getattr(request, "mcp_server", None),
        "tool_name": metadata.get("tool_name"),
        "tool_provider": metadata.get("tool_provider"),
        "tool_server": metadata.get("tool_server"),
        "target_role": metadata.get("target_role"),
        "arguments_digest": metadata.get("arguments_digest"),
    }
    command_digest = (metadata.get("command") or {}).get("digest")
    if command_digest:
        # A shell command's exact text: approving `ls` must not approve
        # `rm -rf ~` (both reached the policy as `sh`). Only commands carry
        # it, so every other approval's digest is unchanged.
        payload["command_digest"] = command_digest
    if isinstance(payload["target"], dict):
        # The tool name inside the target duplicates metadata; keep one copy.
        payload["target"] = {k: v for k, v in payload["target"].items() if v is not None}
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# Sandbox set-up a session asks for when it opens; not a call the agent makes.
# Each holds for the run: a session is opened again after every pause, and
# the mount was asked about a second time after the network pause (the rc7
# gate, D F1).
_SESSION_SETUP = frozenset(
    {
        "sandbox.network.configure",
        "sandbox.environment.set",
        "sandbox.filesystem.mount",
        "sandbox.image.use",
        "sandbox.resources.set",
    }
)


class RunApprovalResolver:
    """Resolves governance asks from decisions recorded on the current run."""

    is_static = False

    async def resolve(self, approval: ApprovalRequest) -> ApprovalResult | None:
        run = current_run()
        if run is None or not run.enabled:
            return None
        digest = request_digest(approval)
        now = utc_now()
        for recorded in run.record.get("approvals", []):
            if recorded["request_digest"] != digest:
                continue
            if (
                recorded["status"] == "used"
                and recorded.get("decision") == "approve"
                and recorded.get("capability") in _SESSION_SETUP
            ):
                # Setting up the sandbox is asked again by every session, and
                # a resume opens a new one (the 0.5.0rc2 gate: the network
                # was asked about twice in one run). The person's yes holds
                # for this exact set-up for the rest of the run.
                return ApprovalResult(
                    approved=True,
                    approval_id=approval.approval_id,
                    resolved_by=recorded["approver"],
                    reason=_decision_reason(recorded),
                    resolved_at=now,
                    metadata={"recorded_approval_id": recorded["approval_id"]},
                )
            if recorded["status"] in {"approved", "denied"}:
                # A decision made in time stands, however late the resume:
                # the expiry limits how long a person has to decide.
                # Read the decision before marking it used (same dict).
                approved = recorded["status"] == "approved"
                reason = _decision_reason(recorded)
                await run.update_approval(
                    recorded["approval_id"],
                    status="used",
                    decision="approve" if approved else "deny",
                    used_at=now.isoformat(),
                    used_for_approval_id=approval.approval_id,
                )
                return ApprovalResult(
                    approved=approved,
                    approval_id=approval.approval_id,
                    resolved_by=recorded["approver"],
                    reason=reason,
                    resolved_at=now,
                    metadata={"recorded_approval_id": recorded["approval_id"]},
                )
            expires_at = _parse(recorded.get("expires_at"))
            undecided_too_long = recorded["status"] == "pending" and (
                expires_at is not None and now > expires_at
            )
            if undecided_too_long or (
                recorded["status"] == "expired" and not recorded.get("used_at")
            ):
                # Nobody decided in time: that is a no. The call is refused,
                # the model is told why, and the run goes on to finish.
                await run.update_approval(
                    recorded["approval_id"],
                    status="expired",
                    decision="deny",
                    used_at=now.isoformat(),
                    used_for_approval_id=approval.approval_id,
                )
                return ApprovalResult(
                    approved=False,
                    approval_id=approval.approval_id,
                    resolved_by="system",
                    reason=f"Approval expired at {recorded.get('expires_at')} without a decision",
                    resolved_at=now,
                    metadata={"recorded_approval_id": recorded["approval_id"], "expired": True},
                )
            if recorded["status"] == "pending":
                # Already waiting for a person. Another call of the same turn
                # asking the same question waits on it too, and is replayed
                # on resume: it was refused and never run (the 0.5.0rc3 gate,
                # two commands that both needed the sandbox network).
                call_id = (approval.metadata or {}).get("tool_call_id")
                waiting = list(recorded.get("also_waiting") or [])
                if call_id and call_id != recorded.get("tool_call_id") and call_id not in waiting:
                    await run.update_approval(
                        recorded["approval_id"], also_waiting=[*waiting, call_id]
                    )
                return None
        metadata = approval.metadata or {}
        COUNTERS.inc("omniserve_approvals_requested_total", risk=approval.risk_level or "unknown")
        await run.add_approval(
            {
                "approval_id": approval.approval_id,
                "request_digest": digest,
                "status": "pending",
                "capability": approval.capability,
                # The actor is part of the digest; an edited call is rebuilt
                # with it, so its approval matches the real request.
                "actor": approval.actor,
                "tool_name": metadata.get("tool_name"),
                "tool_provider": metadata.get("tool_provider"),
                "tool_server": metadata.get("tool_server"),
                "tool_call_id": metadata.get("tool_call_id"),
                "target": to_plain(approval.target) if approval.target is not None else None,
                "risk_level": approval.risk_level,
                "reason": approval.reason,
                "arguments_digest": metadata.get("arguments_digest"),
                # For a shell command: the commands it would run, which the
                # person deciding reads (the target alone says only `sh`).
                "command": _command_for_approver(metadata.get("command")),
                # For a folder operation: the files under an ask rule that the
                # one question covers.
                "covered_files": metadata.get("covered_files"),
                "created_at": now.isoformat(),
                "expires_at": (approval.expires_at or now + DEFAULT_APPROVAL_TTL).isoformat(),
                "approver": None,
                "note": None,
                "edited_arguments": None,
            }
        )
        return None


def _command_for_approver(command: dict[str, Any] | None) -> dict[str, Any] | None:
    if not command or "summary" not in command:
        return None
    return {
        "summary": list(command["summary"]),
        "programs": list(command.get("programs") or []),
        "opaque": bool(command.get("opaque")),
        "opaque_reasons": list(command.get("opaque_reasons") or []),
    }


def _decision_reason(recorded: dict[str, Any]) -> str:
    verb = "Approved" if recorded["status"] == "approved" else "Denied"
    note = recorded.get("note")
    return f"{verb} by {recorded['approver']}" + (f": {note}" if note else "")


async def decide(
    store: Any,
    run_id: str,
    approval_id: str,
    *,
    decision: str,
    approver: str,
    note: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a person's decision on a pending approval; returns the approval.

    The record is read, changed and saved against the version read. A save by
    someone else in between (the run's last heartbeat, which can land after
    the run has returned to wait for this decision) is not the person's
    problem: the record is read again, the approval is checked again (a
    decision made meanwhile by another person stands), and the decision is
    saved against the new version. Found by the step benchmark against
    Postgres, 2026-10-07: one decision in 32 failed with a conflict.
    """
    if decision not in {"approve", "deny"}:
        raise ValueError("decision must be 'approve' or 'deny'")
    if not approver or not str(approver).strip():
        raise ValueError("approver is required")
    for _ in range(_DECIDE_TRIES - 1):
        try:
            return await _decide_once(
                store,
                run_id,
                approval_id,
                decision=decision,
                approver=approver,
                note=note,
                arguments=arguments,
            )
        except RunStateConflict:
            await asyncio.sleep(0.01)
    return await _decide_once(
        store, run_id, approval_id, decision=decision, approver=approver, note=note, arguments=arguments
    )


_DECIDE_TRIES = 10


async def _decide_once(
    store: Any,
    run_id: str,
    approval_id: str,
    *,
    decision: str,
    approver: str,
    note: str | None,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    from omnicoreagent.governance.capabilities import tool_authority_requests

    record = await store.get_run_state(run_id)
    if record is None:
        raise LookupError(f"No run {run_id}")
    approval = next(
        (a for a in record.get("approvals", []) if a["approval_id"] == approval_id), None
    )
    if approval is None:
        raise LookupError(f"No approval {approval_id} on run {run_id}")
    if approval["status"] != "pending":
        raise ValueError(f"Approval {approval_id} is already {approval['status']}")
    expires_at = _parse(approval.get("expires_at"))
    if expires_at is not None and utc_now() > expires_at:
        # Recorded, so the run is no longer held by it: a resume refuses
        # the call as expired and the run finishes.
        approval.update(status="expired", decided_at=utc_now().isoformat())
        version = record.pop("version")
        await store.save_run_state(record, expected_version=version)
        COUNTERS.inc("omniserve_approvals_decided_total", decision="expired")
        raise ValueError(
            f"Approval {approval_id} expired at {approval['expires_at']}; "
            f"resuming the run refuses the call"
        )
    now = utc_now().isoformat()
    approval.update(
        status="approved" if decision == "approve" else "denied",
        decision=decision,
        approver=str(approver),
        note=note,
        decided_at=now,
    )
    if arguments is not None:
        if decision != "approve":
            raise ValueError("arguments can only be edited when approving")
        # The approval now authorizes the edited call, and only that call.
        edited = tool_authority_requests(
            tool_name=approval["tool_name"],
            tool_args=arguments,
            tool_provider=approval["tool_provider"] or "local",
            tool_server=approval.get("tool_server"),
            actor=approval.get("actor") or "agent",
        )
        match = next(
            (r for r in edited if r.capability == approval["capability"]), edited[0]
        )
        approval["original_request_digest"] = approval["request_digest"]
        approval["request_digest"] = request_digest(match)
        approval["edited_arguments"] = dict(arguments)
    version = record.pop("version")
    await store.save_run_state(record, expected_version=version)
    COUNTERS.inc("omniserve_approvals_decided_total", decision=decision)
    return approval
