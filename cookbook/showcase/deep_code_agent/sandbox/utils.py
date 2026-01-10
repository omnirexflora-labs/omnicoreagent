import subprocess
import tarfile
from pathlib import Path
from typing import Dict, Any


def safe_extract_tar(
    tar_path: Path,
    extract_to: Path,
    max_files: int = 1000,
    max_size_bytes: int = 100_000_000,
):
    extract_to = extract_to.resolve()
    with tarfile.open(tar_path, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > max_files:
            raise ValueError("Tarball contains too many files (max 1000)")
        total_size = 0
        for member in members:
            if member.name.startswith("/") or ".." in member.name:
                raise ValueError("Path traversal detected in tarball")
            total_size += member.size
            if total_size > max_size_bytes:
                raise ValueError("Tarball exceeds 100MB size limit")
            dest = (extract_to / member.name).resolve()
            if not str(dest).startswith(str(extract_to)):
                raise ValueError("Path escape detected")
        tar.extractall(path=extract_to)


def safe_git_clone(url: str, target_dir: Path):
    if not url.startswith("https://") or not url.endswith(".git"):
        raise ValueError("Only public HTTPS Git URLs ending in .git are allowed")
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(
        [
            "git",
            "clone",
            "--depth=1",
            "--shallow-submodules",
            "--no-single-branch",
            "--filter=blob:none",
            url,
            str(target_dir),
        ],
        check=True,
        capture_output=True,
        timeout=300,
        env=env,
    )


def _snapshot_dir(path: Path) -> Dict[str, str]:
    """Recursively snapshot all text files in a directory."""
    snapshot = {}
    for file_path in path.rglob("*"):
        if file_path.is_file():
            rel = file_path.relative_to(path)
            try:
                # Only read text files (skip binaries)
                content = file_path.read_text(encoding="utf-8", errors="replace")
                snapshot[str(rel)] = content
            except Exception:
                continue
    return snapshot


def compute_diff(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, Any]:
    added = {k: after[k] for k in after if k not in before}
    deleted = {k: before[k] for k in before if k not in after}
    modified = {
        k: {"before": before[k], "after": after[k]}
        for k in before
        if k in after and before[k] != after[k]
    }
    return {"added": added, "deleted": deleted, "modified": modified}


def create_tarball(
    session_id: str,
    workspace_root: str,
    output_dir: str = "./outputs",
    base_dir: str = "working",
) -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    tar_path = Path(output_dir) / f"{session_id}.tar.gz"
    workspace = Path(workspace_root) / session_id / base_dir
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(workspace, arcname=".")
    return str(tar_path)
