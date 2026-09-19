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

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from omnicoreagent.core.runs import current_run
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
    if isinstance(payload["target"], dict):
        # The tool name inside the target duplicates metadata; keep one copy.
        payload["target"] = {k: v for k, v in payload["target"].items() if v is not None}
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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
            expires_at = _parse(recorded.get("expires_at"))
            if expires_at is not None and now > expires_at and recorded["status"] in {
                "pending",
                "approved",
                "denied",
            }:
                await run.update_approval(recorded["approval_id"], status="expired")
                continue
            if recorded["status"] in {"approved", "denied"}:
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
            if recorded["status"] == "pending":
                return None  # already waiting for a person
        metadata = approval.metadata or {}
        await run.add_approval(
            {
                "approval_id": approval.approval_id,
                "request_digest": digest,
                "status": "pending",
                "capability": approval.capability,
                "tool_name": metadata.get("tool_name"),
                "tool_provider": metadata.get("tool_provider"),
                "tool_server": metadata.get("tool_server"),
                "tool_call_id": metadata.get("tool_call_id"),
                "target": to_plain(approval.target) if approval.target is not None else None,
                "risk_level": approval.risk_level,
                "reason": approval.reason,
                "arguments_digest": metadata.get("arguments_digest"),
                "created_at": now.isoformat(),
                "expires_at": (approval.expires_at or now + DEFAULT_APPROVAL_TTL).isoformat(),
                "approver": None,
                "note": None,
                "edited_arguments": None,
            }
        )
        return None


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
    """Record a person's decision on a pending approval; returns the approval."""
    from omnicoreagent.governance.capabilities import tool_authority_requests

    if decision not in {"approve", "deny"}:
        raise ValueError("decision must be 'approve' or 'deny'")
    if not approver or not str(approver).strip():
        raise ValueError("approver is required")
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
        raise ValueError(f"Approval {approval_id} expired at {approval['expires_at']}")
    now = utc_now().isoformat()
    approval.update(
        status="approved" if decision == "approve" else "denied",
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
        )
        match = next(
            (r for r in edited if r.capability == approval["capability"]), edited[0]
        )
        approval["original_request_digest"] = approval["request_digest"]
        approval["request_digest"] = request_digest(match)
        approval["edited_arguments"] = dict(arguments)
    version = record.pop("version")
    await store.save_run_state(record, expected_version=version)
    return approval
