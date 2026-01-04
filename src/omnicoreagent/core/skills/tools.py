"""
Local tools for Agent Skills interaction.

Provides 2 tools for agents to use skills:
1. read_skill_file - Read any file within a skill directory
2. run_skill_script - Execute a Python script from a skill's scripts/ directory

Uses the ToolRegistry pattern for registration.
"""

import subprocess
import sys
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

if TYPE_CHECKING:
    from omnicoreagent.core.skills.manager import SkillManager


def build_skill_tools(
    skill_manager: "SkillManager", registry: ToolRegistry
) -> ToolRegistry:
    """
    Register skill tools in a ToolRegistry.

    Each tool provides safe, controlled interaction with Agent Skills.

    Args:
        skill_manager: SkillManager instance for skill validation.
        registry: ToolRegistry to register tools into.

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
                    "description": "Name of the skill to read from (from available_skills list)",
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
        - Subject to a timeout (default 30 seconds)
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
    def run_skill_script(
        skill_name: str,
        script_name: str,
        args: Optional[List[str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Execute a script from a skill's scripts/ directory.

        Args:
            skill_name: Name of the skill.
            script_name: Name of the script file.
            args: Optional arguments to pass to the script.
            timeout: Execution timeout in seconds.

        Returns:
            Dict with status, stdout, stderr, and exit_code.
        """
        try:
            skill_root = skill_manager.validate_skill(skill_name)
        except RuntimeError as e:
            return {"status": "error", "message": str(e)}

        scripts_dir = (skill_root / "scripts").resolve()
        script_path = (scripts_dir / script_name).resolve()

        if not str(script_path).startswith(str(scripts_dir)):
            return {"status": "error", "message": "Invalid script path"}

        if not script_path.exists():
            return {"status": "error", "message": f"Script not found: {script_name}"}

        if not script_path.is_file():
            return {"status": "error", "message": f"Not a file: {script_name}"}

        try:
            ext = script_path.suffix.lower()
            cmd_prefix = []

            if ext == ".py":
                cmd_prefix = [sys.executable]
            elif ext == ".sh":
                cmd_prefix = ["bash"]
            elif ext in (".js", ".mjs", ".cjs"):
                cmd_prefix = ["node"]
            elif ext == ".ts":
                cmd_prefix = ["ts-node"]
            elif ext == ".rb":
                cmd_prefix = ["ruby"]
            elif ext == ".pl":
                cmd_prefix = ["perl"]

            full_cmd = cmd_prefix + [str(script_path)] + (args or [])

            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(skill_root),
            )

            return {
                "status": "success" if result.returncode == 0 else "error",
                "data": {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                },
                "message": "Script executed successfully"
                if result.returncode == 0
                else "Script execution failed",
            }
        except FileNotFoundError as e:
            return {
                "status": "error",
                "message": f"Interpreter or script not found: {e}",
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"Execution timed out after {timeout}s",
            }
        except Exception as e:
            return {"status": "error", "message": f"Execution failed: {e}"}

    return registry
