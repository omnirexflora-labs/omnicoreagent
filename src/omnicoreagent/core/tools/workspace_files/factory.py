from threading import Lock

from omnicoreagent.core.tools.workspace_files.base import AbstractWorkspaceFilesBackend
from omnicoreagent.core.tools.workspace_files.storage import WorkspaceFilesBackend

from omnicoreagent.core.utils import logger
from omnicoreagent.core.workspace_config import (
    WorkspaceConfig,
    resolve_workspace_config,
)
from omnicoreagent.core.workspace_storage import create_workspace_storage

_WORKSPACE_FILES_NAMESPACE = "files"

_backend_cache: dict = {}
_cache_lock = Lock()


def create_workspace_files_backend(
    *,
    use_cache: bool = True,
    workspace_config: WorkspaceConfig | dict | None = None,
) -> AbstractWorkspaceFilesBackend:
    """
    Create the workspace files backend from the active workspace storage.

    Args:
        use_cache: If True (default), reuse cached backend instances

    Returns:
        Configured backend instance.
    """
    config = resolve_workspace_config(workspace_config)
    cache_key = config.cache_key(namespace=_WORKSPACE_FILES_NAMESPACE)

    # Check cache first
    if use_cache:
        with _cache_lock:
            if cache_key in _backend_cache:
                logger.debug("Reusing cached workspace files storage")
                return _backend_cache[cache_key]

    # Create new backend
    storage = create_workspace_storage(
        namespace=_WORKSPACE_FILES_NAMESPACE,
        config=config,
    )
    backend = _create_backend_instance(storage)

    # Cache it
    if use_cache:
        with _cache_lock:
            _backend_cache[cache_key] = backend

    return backend


def _create_backend_instance(storage) -> AbstractWorkspaceFilesBackend:
    """Create a new backend instance (internal, no caching)."""
    logger.info("Creating workspace files storage")
    return WorkspaceFilesBackend(storage=storage)


def clear_workspace_files_backend_cache():
    """Clear the backend cache (useful for testing or reconfiguration)."""
    with _cache_lock:
        _backend_cache.clear()
        logger.info("Workspace files cache cleared")
