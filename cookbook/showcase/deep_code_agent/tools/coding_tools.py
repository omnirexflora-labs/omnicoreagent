import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any


from sandbox.utils import (
    safe_extract_tar,
    safe_git_clone,
    create_tarball,
    _snapshot_dir,
    compute_diff,
)
from observability_globals import metrics, audit, CONFIG
from observability import perf


def register_coding_tools(tool_registry, runner_instance):
    cfg = CONFIG
    workspace_root = Path(cfg.coding.workspace_root)
    session_dir = workspace_root / runner_instance.session_id
    session_id = runner_instance.session_id

    def log_tool_call(tool_name: str, params: Dict[str, Any]):
        audit.tool_call(tool_name, session_id, params)

    @tool_registry.register_tool(
        name="ingest_codebase",
        description="Load codebase from a .tar.gz file or a public Git repo (.git URL).",
        inputSchema={
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Path to .tar.gz or https://.../repo.git",
                }
            },
            "required": ["source_path"],
        },
    )
    @perf(metrics)
    def ingest_codebase(source_path: str) -> Dict[str, Any]:
        log_tool_call("ingest_codebase", {"source_path": source_path})

        if source_path.endswith(".tar.gz"):
            source_type = "tarball"
        elif source_path.endswith(".git") and source_path.startswith("https://"):
            source_type = "git"
        else:
            return {
                "status": "error",
                "message": "Invalid source. Must be a .tar.gz file or a public Git URL ending in .git",
            }

        original_dir = session_dir / "original"
        working_dir = session_dir / "working"

        if original_dir.exists() or working_dir.exists():
            return {
                "status": "error",
                "message": "Codebase already ingested for this session",
            }

        session_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Ingest into original/
            original_dir.mkdir()
            if source_type == "tarball":
                tar_path = Path(source_path)
                if not tar_path.is_file():
                    raise FileNotFoundError("Tarball not found")
                safe_extract_tar(tar_path, original_dir)
            else:
                safe_git_clone(source_path, original_dir)

            # Clone to working/
            shutil.copytree(original_dir, working_dir, dirs_exist_ok=False)

            return {
                "status": "success",
                "message": f"Codebase loaded from {source_type}",
            }

        except Exception as e:
            # Clean up on failure
            shutil.rmtree(original_dir, ignore_errors=True)
            shutil.rmtree(working_dir, ignore_errors=True)
            if not any(session_dir.iterdir()):
                shutil.rmtree(session_dir, ignore_errors=True)
            return {"status": "error", "message": str(e)}

    @tool_registry.register_tool(
        name="execute_in_sandbox",
        description="Run a command (Python, JS, Bash, etc.) in a hardened Docker sandbox.",
        inputSchema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )
    @perf(metrics)
    async def execute_in_sandbox(command: str) -> Dict[str, Any]:
        log_tool_call("execute_in_sandbox", {"command": command})

        working_dir = session_dir / "working"
        if not working_dir.exists():
            return {
                "status": "error",
                "message": "Working directory not found. Did you ingest code first?",
            }
        working_dir_abs = working_dir.resolve()
        if not working_dir_abs.is_absolute():
            raise RuntimeError(f"Working dir is not absolute: {working_dir_abs}")
        return await runner_instance.sandbox_executor.execute(
            session_id=runner_instance.session_id,
            working_dir_host=str(working_dir_abs),
            command=command,
        )

    @tool_registry.register_tool(
        name="export_updated_codebase",
        description="Export the current sandbox state as a .tar.gz file.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    @perf(metrics)
    def export_updated_codebase() -> Dict[str, Any]:
        log_tool_call("export_updated_codebase", {})
        try:
            # Export the WORKING copy
            tar_path = create_tarball(
                session_id, str(workspace_root), base_dir="working"
            )
            return {"status": "success", "data": tar_path}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @tool_registry.register_tool(
        name="create_git_branch",
        description="Create a new Git branch with current changes (only if original was Git).",
        inputSchema={
            "type": "object",
            "properties": {"branch_name": {"type": "string"}},
            "required": ["branch_name"],
        },
    )
    @perf(metrics)
    def create_git_branch(branch_name: str) -> Dict[str, Any]:
        log_tool_call("create_git_branch", {"branch_name": branch_name})
        git_dir = session_dir / ".git"
        if not git_dir.exists():
            return {"status": "error", "message": "Not a Git repo"}

        try:
            subprocess.run(
                ["git", "checkout", "-b", branch_name], cwd=session_dir, check=True
            )
            subprocess.run(["git", "add", "."], cwd=session_dir, check=True)
            subprocess.run(
                ["git", "config", "user.name", "DeepCoder"], cwd=session_dir, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "deepcoder@agent.local"],
                cwd=session_dir,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "AI-generated changes"],
                cwd=session_dir,
                check=True,
            )
            return {
                "status": "success",
                "message": f"git push origin {branch_name}",
            }
        except subprocess.CalledProcessError as e:
            return {
                "status": "error",
                "message": e.stderr.decode() if e.stderr else str(e),
            }

    @tool_registry.register_tool(
        name="compute_final_diff",
        description="Compute full diff between original and working codebase",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    @perf(metrics)
    def compute_final_diff() -> Dict[str, Any]:
        original_dir = session_dir / "original"
        working_dir = session_dir / "working"
        if not (original_dir.exists() and working_dir.exists()):
            return {"status": "error", "message": "Code not ingested"}

        before = _snapshot_dir(original_dir)
        after = _snapshot_dir(working_dir)
        diff = compute_diff(before, after)
        return {"status": "success", "data": diff}
