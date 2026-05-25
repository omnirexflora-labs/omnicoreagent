from __future__ import annotations

from typing import Protocol

from omnicoreagent.governance.models import ApprovalRequest, ApprovalResult


class ApprovalResolver(Protocol):
    async def resolve(self, request: ApprovalRequest) -> ApprovalResult | None:
        """Resolve an approval request or return None to leave it pending."""


class DenyAllApprovalResolver:
    async def resolve(self, request: ApprovalRequest) -> ApprovalResult:
        return ApprovalResult(
            approved=False,
            approval_id=request.approval_id,
            resolved_by="system",
            reason="No approval resolver accepted the request.",
        )


class StaticApprovalResolver:
    is_static = True

    def __init__(
        self,
        *,
        approved: bool,
        resolved_by: str = "static",
        reason: str | None = None,
    ) -> None:
        self.approved = approved
        self.resolved_by = resolved_by
        self.reason = reason

    async def resolve(self, request: ApprovalRequest) -> ApprovalResult:
        return ApprovalResult(
            approved=self.approved,
            approval_id=request.approval_id,
            resolved_by=self.resolved_by,
            reason=self.reason,
        )
