"""
Local tools for Agent Skills interaction.

Provides 2 tools for agents to use skills:
1. read_skill_file - Read any file within a skill directory
2. run_skill_script - Execute a Python script from a skill's scripts/ directory

Uses the ToolRegistry pattern for registration.
"""

import asyncio
import os
import signal
import sys
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

if TYPE_CHECKING:
    from omnicoreagent.core.skills.manager import SkillManager


# Interpreter per script extension on the host (unchanged from before).
_INTERPRETERS = {
    ".py": [sys.executable],
    ".sh": ["bash"],
    ".bash": ["bash"],
    ".js": ["node"],
    ".mjs": ["node"],
    ".cjs": ["node"],
    ".ts": ["ts-node"],
    ".rb": ["ruby"],
    ".pl": ["perl"],
}
# In a sandbox the image provides the interpreters; minimal images have `sh`
# but not `bash`, and Python is `python3`.
_SANDBOX_INTERPRETERS = {**_INTERPRETERS, ".py": ["python3"], ".sh": ["sh"]}
# Skill files copied into a sandbox, at most this many bytes in total.
_MAX_SKILL_BYTES = 20 * 1024 * 1024


# What a host script receives from the agent's environment by default: enough
# to find programs and handle text, never the agent's credentials.
_MINIMAL_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "TERM", "SYSTEMROOT", "PATHEXT", "COMSPEC")


def _script_environment(passthrough: List[str]) -> Dict[str, str]:
    names = {*_MINIMAL_ENV, *passthrough}
    return {
        key: value
        for key, value in os.environ.items()
        if key in names or key.startswith("LC_")
    }


async def _run_on_host(
    command: List[str], cwd, timeout: int, env: Dict[str, str] | None = None
) -> Dict[str, Any]:
    """Run a skill script on the host without blocking the event loop."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=_script_environment([]) if env is None else env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout kills the script's children too
            # (a surviving child would hold the output pipes open).
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return {"status": "error", "message": f"Interpreter or script not found: {e}"}
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_group(process)
        return {"status": "error", "message": f"Execution timed out after {timeout}s"}
    except asyncio.CancelledError:
        await _kill_group(process)
        raise
    return _script_result(
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        surface="host",
    )


async def _kill_group(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def _run_in_sandbox(
    scope, skill_root, skill_name: str, interpreter: List[str], relative: str,
    args: List[str], timeout: int,
) -> Dict[str, Any]:
    """Copy the skill into the run's sandbox and run the script there."""
    runtime = getattr(scope.service.governance_engine, "sandbox_runtime", None)
    if getattr(runtime, "execution_surface", "sandbox") == "host":
        # The local sandbox runs on this machine, where the skill already is:
        # run it in place (still authorized as a host command), copying nothing
        # into the user's working directory.
        result = await scope.execute(
            [*interpreter, relative, *args],
            cwd=str(skill_root.resolve()),
            timeout_seconds=timeout,
        )
        if result.timed_out:
            return {"status": "error", "message": f"Execution timed out after {timeout}s"}
        return _script_result(result.exit_code, result.stdout, result.stderr, surface="host")
    files, total = {}, 0
    for path in sorted(skill_root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
            if total > _MAX_SKILL_BYTES:
                return {"status": "error", "message": "Skill is too large to copy into the sandbox"}
            files[f"/workspace/.skills/{skill_name}/{path.relative_to(skill_root).as_posix()}"] = path.read_bytes()
    await scope.upload(files)
    result = await scope.execute(
        [*interpreter, relative, *args],
        cwd=f"/workspace/.skills/{skill_name}",
        timeout_seconds=timeout,
    )
    if result.timed_out:
        return {"status": "error", "message": f"Execution timed out after {timeout}s"}
    return _script_result(result.exit_code, result.stdout, result.stderr, surface="sandbox")


def _script_result(exit_code, stdout: str, stderr: str, *, surface: str) -> Dict[str, Any]:
    return {
        "status": "success" if exit_code == 0 else "error",
        "data": {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "execution_surface": surface,
        },
        "message": "Script executed successfully" if exit_code == 0 else "Script execution failed",
    }


def build_skill_tools(
    skill_manager: "SkillManager",
    registry: ToolRegistry,
    env_passthrough: List[str] | None = None,
) -> ToolRegistry:
    """
    Register skill tools in a ToolRegistry.

    Each tool provides safe, controlled interaction with Agent Skills.

    Args:
        skill_manager: SkillManager instance for skill validation.
        registry: ToolRegistry to register tools into.
        env_passthrough: Environment variables a host script receives beyond
            the minimal set; nothing else from the agent's environment.

    Returns:
        The registry with skill tools added.
    """

    @registry.register_tool(
        name="read_skill_file",
        description="""
        Read a file from a skill directory.
        
        Use this to:
        - Read SKILL.md for skill instructions and guidance
        - Read files in references/ for documentation
        - Read files in assets/ for templates and resources
        
        The path is scoped to the skill directory for security.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to read from the available skills catalog",
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative path to file within skill directory. Examples: 'SKILL.md', 'references/GUIDE.md', 'assets/template.txt'",
                },
            },
            "required": ["skill_name", "file_path"],
            "additionalProperties": False,
        },
    )
    def read_skill_file(skill_name: str, file_path: str) -> Dict[str, Any]:
        """
        Read a file from within a skill directory.

        Args:
            skill_name: Name of the skill.
            file_path: Relative path to the file within the skill directory.

        Returns:
            Dict with status and file content or error message.
        """
        try:
            skill_root = skill_manager.validate_skill(skill_name)
        except RuntimeError as e:
            return {"status": "error", "error": str(e)}

        file_path = file_path.strip()
        if not file_path:
            return {"status": "error", "error": "Missing file path"}

        target = (skill_root / file_path).resolve()

        if not str(target).startswith(str(skill_root)):
            return {
                "status": "error",
                "message": "Access outside skill directory is not allowed",
            }

        if not target.exists():
            return {"status": "error", "message": f"File not found: {file_path}"}

        if not target.is_file():
            return {"status": "error", "message": f"Not a file: {file_path}"}

        try:
            content = target.read_text(encoding="utf-8")
            return {
                "status": "success",
                "data": content,
                "message": "File read successfully",
            }
        except Exception as e:
            return {"status": "error", "message": f"Failed to read file: {e}"}

    @registry.register_tool(
        name="run_skill_script",
        description="""
        Execute a script from a skill's scripts/ directory.
        
        Scripts are:
        - Sandboxed to run from the skill directory
        - Subject to the agent's tool timeout (180 seconds by default)
        - Only accessible from the scripts/ subdirectory
        
        Use this when a skill's SKILL.md references a script to run.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill"},
                "script_name": {
                    "type": "string",
                    "description": "Name of the script file (e.g., 'search.py')",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional arguments to pass to the script",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default: 30)",
                },
            },
            "required": ["skill_name", "script_name"],
            "additionalProperties": False,
        },
    )
    async def run_skill_script(
        skill_name: str,
        script_name: str,
        args: Optional[List[str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Execute a script from a skill's scripts/ directory.

        Runs in the agent's sandbox when it has one (the skill's files are
        copied in); otherwise on the host, asynchronously, and recorded as
        host execution. Governed as ``skill.script.run``.
        """
        try:
            skill_root = skill_manager.validate_skill(skill_name)
        except RuntimeError as e:
            return {"status": "error", "message": str(e)}

        scripts_dir = (skill_root / "scripts").resolve()
        script_path = (scripts_dir / script_name).resolve()
        if not script_path.is_relative_to(scripts_dir) or script_path == scripts_dir:
            return {"status": "error", "message": "Invalid script path"}
        if not script_path.exists():
            return {"status": "error", "message": f"Script not found: {script_name}"}
        if not script_path.is_file():
            return {"status": "error", "message": f"Not a file: {script_name}"}

        suffix = script_path.suffix.lower()
        relative = script_path.relative_to(skill_root).as_posix()

        from omnicoreagent.sandbox.scope import current_execution

        scope = current_execution()
        if scope is not None:
            return await _run_in_sandbox(
                scope,
                skill_root,
                skill_name,
                _SANDBOX_INTERPRETERS.get(suffix, []),
                relative,
                args or [],
                timeout,
            )
        return await _run_on_host(
            [*_INTERPRETERS.get(suffix, []), str(script_path), *(args or [])],
            skill_root,
            timeout,
            env=_script_environment(env_passthrough or []),
        )

    # Governed by their own capabilities (skill.files.read, skill.script.run),
    # not as ordinary local tools.
    for name in ("read_skill_file", "run_skill_script"):
        registry.mark_internal_tool_provider(name, "skill")
    return registry
