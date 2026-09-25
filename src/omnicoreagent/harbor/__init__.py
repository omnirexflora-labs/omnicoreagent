"""Running OmniCoreAgent as a Harbor (Terminal-Bench) agent.

Harbor installs an agent **into the task container** and the task is solved by
running commands there, so the sandbox provider is ``local`` and the working
directory is the task's own. Harbor loads an agent by import path, so this ships
with the runtime it drives:

    harbor run --agent omnicoreagent.harbor:OmniCoreAgentHarbor \
      --model openai/gpt-5.6-terra --task-id <task>

The adapter itself is in ``agent``, which imports Harbor and is therefore only
importable where Harbor is installed. Everything it decides — the agent file
written into the container, the command that runs it, what a finished run says,
and the trajectory Harbor reads — is in ``trial``, which imports nothing of
Harbor's and can be tested anywhere.
"""

from omnicoreagent.harbor.trial import (
    ATIF_SCHEMA_VERSION,
    agent_file_source,
    atif_trajectory,
    run_command,
    usage_from_result,
)

__all__ = [
    "ATIF_SCHEMA_VERSION",
    "agent_file_source",
    "atif_trajectory",
    "run_command",
    "usage_from_result",
]


def __getattr__(name: str):
    """``OmniCoreAgentHarbor`` needs Harbor, so it is imported on use."""
    if name == "OmniCoreAgentHarbor":
        from omnicoreagent.harbor.agent import OmniCoreAgentHarbor

        return OmniCoreAgentHarbor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
