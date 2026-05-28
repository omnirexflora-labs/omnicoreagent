from __future__ import annotations


class SandboxRuntimeError(Exception):
    """Base error for sandbox runtime failures."""


class SandboxUnsupportedError(SandboxRuntimeError):
    """Raised when a sandbox adapter cannot satisfy a requested operation."""


class SandboxSessionNotFoundError(SandboxRuntimeError):
    """Raised when a sandbox session id is unknown to the adapter."""
