"""Check that a sandbox which must have no network really has none.

A hosted service is asked for a sandbox with no internet access, and both E2B
and Daytona accept that request and record it — and, on the accounts this was
tested against, still hand back a sandbox that reaches the internet. A promise
of isolation that the provider does not keep is worse than no sandbox at all:
a policy that allows execution only because it is isolated would be silently
wrong.

So the adapter checks the sandbox it was given, from inside it, by trying one
outbound connection to a public address. Reaching it means the sandbox is not
isolated and is refused; a sandbox with nothing that can run the check is
refused too, because it cannot be shown to be isolated. An application that
knows its provider and accepts the risk can turn the check off.
"""

from __future__ import annotations

from omnicoreagent.sandbox.errors import SandboxUnsupportedError

# A raw address, so the check is of outbound traffic and not of name lookup.
UNREACHABLE_TARGET = ("1.1.1.1", 80)
NO_TOOL_EXIT_CODE = 127
CHECK_TIMEOUT_SECONDS = 15

_CONNECT = (
    "import socket;socket.create_connection(('{host}',{port}),5)".format(
        host=UNREACHABLE_TARGET[0], port=UNREACHABLE_TARGET[1]
    )
)
# Exit 0 means the sandbox reached the internet; 127 means nothing could check.
NETWORK_CHECK_COMMAND = (
    f'if command -v python3 >/dev/null 2>&1; then python3 -c "{_CONNECT}"; '
    f'elif command -v python >/dev/null 2>&1; then python -c "{_CONNECT}"; '
    "else exit 127; fi"
)


def isolation_verdict(exit_code: int) -> str:
    if exit_code == 0:
        return "reachable"
    if exit_code == NO_TOOL_EXIT_CODE:
        return "unknown"
    return "isolated"


def refuse_open_sandbox(provider: str, verdict: str) -> None:
    """Raise unless the sandbox was shown to have no network."""
    if verdict == "isolated":
        return
    if verdict == "reachable":
        raise SandboxUnsupportedError(
            f"The {provider} sandbox was created with no network access, but it "
            "still reached the internet: this account or plan does not enforce "
            "network isolation. Use a provider that does, allow the network in "
            "the manifest if the work needs it, or set the sandbox option "
            "verify_network_isolation=false to accept an unchecked sandbox."
        )
    raise SandboxUnsupportedError(
        f"The {provider} sandbox's network could not be checked: the image has no "
        "python to run the check with. Use an image that has one, or set the "
        "sandbox option verify_network_isolation=false to accept an unchecked "
        "sandbox."
    )
