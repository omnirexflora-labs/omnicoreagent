from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
from omnicoreagent.core.tools.memory_tool.storage import WorkspaceMemoryBackend
from threading import Lock

from decouple import config
from omnicoreagent.core.utils import logger
from omnicoreagent.core.workspace_storage import create_workspace_storage

_backend_cache: dict = {}
_cache_lock = Lock()


def create_memory_backend(
    *,
    use_cache: bool = True,
) -> AbstractMemoryBackend:
    """
    Create the workspace memory backend from the active workspace storage.

    Args:
        use_cache: If True (default), reuse cached backend instances

    Returns:
        Configured backend instance.
    """
    cache_key = _backend_cache_key()

    # Check cache first
    if use_cache:
        with _cache_lock:
            if cache_key in _backend_cache:
                logger.debug("Reusing cached workspace memory storage")
                return _backend_cache[cache_key]

    # Create new backend
    storage = create_workspace_storage(namespace="memories")
    backend = _create_backend_instance(storage)

    # Cache it
    if use_cache:
        with _cache_lock:
            _backend_cache[cache_key] = backend
    
    return backend


def _backend_cache_key() -> tuple:
    workspace_backend = config("OMNICOREAGENT_WORKSPACE_BACKEND", default="local")
    workspace_backend = workspace_backend.lower().strip()
    prefix = config("OMNICOREAGENT_WORKSPACE_PREFIX", default="workspace").strip("/")

    if workspace_backend == "local":
        from omnicoreagent.core.workspace import get_memories_dir

        return (workspace_backend, get_memories_dir())

    if workspace_backend == "s3":
        return (
            workspace_backend,
            config("AWS_S3_BUCKET", default=None),
            config("AWS_REGION", default=None),
            config("AWS_ENDPOINT_URL", default=None),
            prefix,
        )

    if workspace_backend == "r2":
        return (
            workspace_backend,
            config("R2_BUCKET_NAME", default=None),
            config("R2_ACCOUNT_ID", default=None),
            prefix,
        )

    return (workspace_backend,)


def _create_backend_instance(storage) -> AbstractMemoryBackend:
    """Create a new backend instance (internal, no caching)."""
    logger.info("Creating workspace memory storage")
    return WorkspaceMemoryBackend(storage=storage)


def clear_backend_cache():
    """Clear the backend cache (useful for testing or reconfiguration)."""
    with _cache_lock:
        _backend_cache.clear()
        logger.info("Workspace memory cache cleared")
