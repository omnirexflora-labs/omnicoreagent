import asyncio
from pathlib import Path
from typing import Dict, Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound


class SandboxExecutor:
    def __init__(self, workspace_root: str, timeout: int = 30, memory_mb: int = 512):
        self.workspace_root = Path(workspace_root)
        self.timeout = timeout
        self.memory_bytes = memory_mb * 1024 * 1024
        self.docker_client = docker.from_env()
        self._ensure_image_exists()
        self._active_containers = {}

    def _ensure_image_exists(self):
        try:
            self.docker_client.images.get("deepcoder-sandbox:latest")
        except ImageNotFound:
            raise RuntimeError(
                "Sandbox image 'deepcoder-sandbox:latest' not found. Build it first."
            )

    async def ensure_container_running(
        self, session_id: str, working_dir_host: str
    ) -> str:
        container_name = f"sandbox_{session_id}"

        # Resolve to absolute path
        working_dir_host = Path(working_dir_host).resolve()
        if not working_dir_host.is_absolute():
            raise ValueError("working_dir_host must be absolute")
        if not working_dir_host.exists():
            raise FileNotFoundError(f"Working directory not found: {working_dir_host}")

        # If we already have this container cached and it's running, use it
        if session_id in self._active_containers:
            try:
                container = self._active_containers[session_id]
                container.reload()
                if container.status == "running":
                    return container_name
            except (APIError, NotFound):
                # Container was removed externally, remove from cache
                self._active_containers.pop(session_id, None)

        try:
            # Try to get existing container
            container = self.docker_client.containers.get(container_name)
            container.reload()

            # Try to start it (handles both stopped and already-running cases)
            try:
                container.start()
            except APIError as e:
                # "already running" is not an error for us
                if "already running" not in str(e).lower():
                    raise

            # Reload after start attempt to get current status
            container.reload()
            self._active_containers[session_id] = container
            return container_name

        except NotFound:
            # Container doesn't exist — create and start it
            container = self.docker_client.containers.create(
                "deepcoder-sandbox:latest",
                command=["tail", "-f", "/dev/null"],
                volumes={
                    str(working_dir_host): {
                        "bind": "/home/coder/workspace",
                        "mode": "rw",
                    }
                },
                name=container_name,
                # Basic memory limit only
                mem_limit=self.memory_bytes,
                detach=True,
            )
            container.start()

            # Wait a moment for container to fully start
            await asyncio.sleep(0.2)
            container.reload()

            # Verify it actually started
            if container.status != "running":
                # Get logs for debugging
                logs = container.logs().decode("utf-8", errors="replace")
                container.remove(force=True)
                raise RuntimeError(
                    f"Container failed to start. Status: {container.status}\n"
                    f"Logs:\n{logs}"
                )

            chown_result = container.exec_run(
                cmd=["chown", "-R", "coder:coder", "/home/coder/workspace"], user="root"
            )
            if chown_result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to set workspace permissions: {chown_result.output.decode('utf-8', errors='replace')}"
                )

            self._active_containers[session_id] = container
            return container_name

    async def execute(
        self,
        session_id: str,
        working_dir_host: str,
        command: str,
    ) -> Dict[str, Any]:
        """Execute a command in the long-lived sandbox container."""
        try:
            await self.ensure_container_running(session_id, working_dir_host)
            container = self._active_containers[session_id]

            # Verify container is still running before executing
            container.reload()
            if container.status != "running":
                raise RuntimeError(
                    f"Container not running (status: {container.status})"
                )

            exec_result = container.exec_run(
                cmd=["bash", "-c", f"timeout {self.timeout} {command}"],
                workdir="/home/coder/workspace",
                demux=True,
            )

            stdout = (
                exec_result.output[0].decode(errors="replace")
                if exec_result.output[0]
                else ""
            )
            stderr = (
                exec_result.output[1].decode(errors="replace")
                if exec_result.output[1]
                else ""
            )
            exit_code = exec_result.exit_code

            return {
                "status": "success",
                "data": {
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                },
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def cleanup_session(self, session_id: str):
        """Clean up container when session ends."""
        container_name = f"sandbox_{session_id}"
        self._cleanup_container(container_name)
        self._active_containers.pop(session_id, None)

    def _cleanup_container(self, name: str):
        try:
            c = self.docker_client.containers.get(name)
            c.stop(timeout=2)
            c.remove(force=True)
        except (APIError, NotFound):
            pass
