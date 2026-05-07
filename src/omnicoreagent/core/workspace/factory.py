from threading import Lock

from omnicoreagent.core.logging import logger
from omnicoreagent.core.workspace.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.workspace.config import (
    WorkspaceConfig,
    resolve_workspace_config,
)
from omnicoreagent.core.workspace.files import WorkspaceFilesBackend
from omnicoreagent.core.workspace.manager import Workspace

_WORKSPACE_FILES_NAMESPACE = "files"

_backend_cache: dict = {}
_cache_lock = Lock()


def create_workspace_files_backend(
    *,
    use_cache: bool = True,
    workspace: Workspace | None = None,
    workspace_config: WorkspaceConfig | dict | None = None,
) -> AbstractWorkspaceFilesBackend:
    """
    Create workspace file operations from the active workspace files area.

    Args:
        use_cache: If True (default), reuse cached workspace files adapters

    Returns:
        Configured workspace files adapter.
    """
    if workspace is not None:
        return _create_backend_instance(workspace.files)

    config = resolve_workspace_config(workspace_config)
    cache_key = config.cache_key(namespace=_WORKSPACE_FILES_NAMESPACE)

    # Check cache first
    if use_cache:
        with _cache_lock:
            if cache_key in _backend_cache:
                logger.debug("Reusing cached workspace files storage")
                return _backend_cache[cache_key]

    backend = _create_backend_instance(Workspace.from_config(config).files)

    # Cache it
    if use_cache:
        with _cache_lock:
            _backend_cache[cache_key] = backend

    return backend


def _create_backend_instance(storage) -> AbstractWorkspaceFilesBackend:
    """Create a new workspace files adapter (internal, no caching)."""
    logger.info("Creating workspace files storage")
    return WorkspaceFilesBackend(storage=storage)


def clear_workspace_files_backend_cache():
    """Clear the backend cache (useful for testing or reconfiguration)."""
    with _cache_lock:
        _backend_cache.clear()
        logger.info("Workspace files cache cleared")
