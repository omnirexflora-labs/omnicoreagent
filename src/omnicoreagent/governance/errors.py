from __future__ import annotations


class GovernanceError(Exception):
    """Base error for governed execution failures."""

    code = "governance_error"

    def __init__(self, message: str = "", *, metadata: dict | None = None) -> None:
        self.message = message or self.__class__.__name__
        self.metadata = dict(metadata or {})
        super().__init__(self.message)


class PolicyLoadError(GovernanceError):
    """Raised when a policy cannot be loaded safely."""

    code = "policy_load_error"


class PolicyEvaluationError(GovernanceError):
    """Raised when policy evaluation fails closed."""

    code = "policy_evaluation_error"


class PolicyDeniedError(GovernanceError):
    """Raised when policy denies an authority request."""

    code = "policy_denied"


class ApprovalRequiredError(GovernanceError):
    """Raised when an authority request needs approval before side effects."""

    code = "approval_required"


class ApprovalExpiredError(GovernanceError):
    """Raised when an approval exists but is no longer valid."""

    code = "approval_expired"


class ApprovalInvalidError(GovernanceError):
    """Raised when an approval resolver returns a mismatched result."""

    code = "approval_invalid"


class AuditRequiredError(GovernanceError):
    """Raised when a policy requires authority evidence that cannot be recorded."""

    code = "audit_required"


class SandboxRequiredError(GovernanceError):
    """Raised when execution requires sandboxing that is unavailable."""

    code = "sandbox_required"


class BudgetExceededError(GovernanceError):
    """Raised when an authority request exceeds its governed budget."""

    code = "budget_exceeded"


class UnknownCapabilityError(GovernanceError):
    """Raised when an unknown capability is denied by policy."""

    code = "unknown_capability"


class UngovernedCapabilityError(GovernanceError):
    """Raised when a runtime surface is not governed in the current phase."""

    code = "ungoverned_capability"


class ToolArgumentsInvalid(GovernanceError):
    """A call whose arguments cannot be read into an authority request (an
    empty path): rejected before any rule could decide it, not denied."""

    code = "invalid_arguments"
