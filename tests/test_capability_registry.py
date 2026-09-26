"""Every capability the runtime can ask about is listed, with what it means.

The research-team app built from the docs alone could not find the name to
write a rule for: the security model page promised "what every capability
means" and listed none. The policy reference is now generated from
`CAPABILITIES`; this test keeps it complete.
"""

from __future__ import annotations

import re
from pathlib import Path

from omnicoreagent.governance import capabilities as caps
from omnicoreagent.governance.capabilities import CAPABILITIES

SRC = Path(__file__).resolve().parents[1] / "src" / "omnicoreagent"
LITERAL = re.compile(
    r'(?:capability\s*=\s*|return |else )"([a-z_]+\.[a-z_.]+)"|"([a-z_]+\.[a-z_.]+)" if '
)
SANDBOX = re.compile(r'"(sandbox\.[a-z_.]+|process\.exec)"')


def _asked_for() -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "capabilit" not in text and "_sandbox_scope_request" not in text:
            continue
        for match in LITERAL.finditer(text):
            names.add(match.group(1) or match.group(2))
        if path.name == "execution.py":
            names.update(SANDBOX.findall(text))
    names |= {f"network.http.{m}" for m in caps.HTTP_METHODS}
    names |= {f"filesystem.{a}" for a in caps.FILESYSTEM_ACTIONS}
    names |= {f"background.task.{a}" for a in caps.BACKGROUND_TASK_ACTIONS}
    names |= {f"background.run.{a}" for a in caps.BACKGROUND_RUN_ACTIONS}
    # Span kinds and event names share the dotted style; they are not asked about.
    return {n for n in names if not n.startswith(("telemetry.", "memory.", "tool.batch", "tool.call", "tool.offload", "subagent.run", "workspace.read", "workspace.write", "workspace.delete", "mcp.tool"))}


def test_every_capability_the_runtime_asks_about_is_listed():
    missing = sorted(_asked_for() - set(CAPABILITIES))
    assert not missing, f"add to CAPABILITIES: {missing}"


def test_every_listed_capability_is_one_the_runtime_asks_about():
    stale = sorted(set(CAPABILITIES) - _asked_for())
    assert not stale, f"no longer asked about: {stale}"


def test_every_capability_says_what_it_lets_the_agent_do():
    assert all(len(text) > 20 for text in CAPABILITIES.values())
