"""The built-in `execute` tool: shell commands in the agent's sandbox.

Registered only when the agent has a sandbox that can execute. Every command
is authorized by governance (`sandbox.execute` for the tool call, then
`process.exec` inside the sandbox) and runs in the run's single sandbox
session, so files written by one command are there for the next.
"""

from __future__ import annotations

from typing import Any

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

DEFAULT_TIMEOUT_SECONDS = 60

_SANDBOX_DESCRIPTION = (
    "Run a shell command in an isolated sandbox and return its exit code, "
    "stdout, and stderr. The sandbox has no network access unless your "
    "policy allows it, cannot see the host's files or credentials, and "
    "keeps files you create in its working directory until the task "
    "ends. When workspace files are enabled, they are in the working "
    "directory and text files the command creates or changes are saved "
    "back to the workspace. Use it to run code, scripts, and command-line tools."
)
# The local sandbox is not isolated, and the model is not told it is.
_HOST_DESCRIPTION = (
    "Run a shell command on this machine and return its exit code, stdout, "
    "and stderr. It runs in the working directory, directly on the machine "
    "you are working on: it is not isolated, so it can change real files and "
    "reach the network, and what it changes stays changed. When workspace "
    "files are enabled, they are in the working directory and text files the "
    "command creates or changes are saved back to the workspace. Use it to "
    "run code, scripts, and command-line tools."
)


def build_execution_tools(
    registry: ToolRegistry, *, max_timeout_seconds: int, on_host: bool = False
) -> ToolRegistry:
    max_timeout = max(1, int(max_timeout_seconds))

    @registry.register_tool(
        name="execute",
        description=_HOST_DESCRIPTION if on_host else _SANDBOX_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command, run with `sh -c` in the working directory.",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Seconds before the command is stopped (1 to {max_timeout}).",
                    "minimum": 1,
                    "maximum": max_timeout,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )
    async def execute(command: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
        from omnicoreagent.sandbox.scope import current_execution

        scope = current_execution()
        if scope is None:
            return {"status": "error", "message": "No sandbox is available for this run."}
        if not command.strip():
            return {"status": "error", "message": "The command is empty."}
        limit = min(max(1, int(timeout)), max_timeout)
        result = await scope.execute(["sh", "-c", command], timeout_seconds=limit)
        return execution_result(result, limit)

    registry.mark_internal_tool_provider("execute", "sandbox")
    return registry


def execution_result(result: Any, limit: int | None = None) -> dict[str, Any]:
    """A tool result for a finished command: non-zero exit and timeouts are errors."""
    data = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
        "execution_surface": result.metadata.get("execution_surface", "sandbox"),
    }
    for key in ("stdout_truncated", "stderr_truncated"):
        if result.metadata.get(key):
            data[key] = True
    workspace = result.metadata.get("workspace")
    if workspace and (workspace.get("written") or workspace.get("skipped")):
        data["workspace_files"] = workspace
    if result.metadata.get("session_terminated"):
        data["sandbox_lost"] = True
        why = result.stderr.strip() or "it stopped"
        return {
            "status": "error",
            "data": data,
            "message": (
                f"The sandbox was lost during this command ({why}). Files created "
                "in it are gone; the next command runs in a fresh sandbox with the "
                "workspace files copied in again."
            ),
        }
    if result.timed_out:
        return {"status": "error", "data": data, "message": f"Command timed out after {limit}s"}
    if result.exit_code != 0:
        return {"status": "error", "data": data, "message": f"Command exited with {result.exit_code}"}
    return {"status": "success", "data": data}
