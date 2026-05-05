"""
Simple memory backend factory for string-based configuration.
Creates backends from string type ("local", "s3", "r2") using environment variables.
Caches backends to avoid re-initializing connections.
"""

from omnicoreagent._optional import load_optional
from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
from omnicoreagent.core.tools.memory_tool.local_storage import LocalMemoryBackend
from decouple import config
from omnicoreagent.core.utils import logger
from omnicoreagent.core.workspace import get_memories_dir
from threading import Lock

# Resolved from workspace module
# Override with OMNICOREAGENT_MEMORY_DIR or OMNICOREAGENT_WORKSPACE_DIR env vars
LOCAL_MEMORY_BASE_DIR = get_memories_dir()

# Cache for backend instances to avoid re-initialization
_backend_cache: dict = {}
_cache_lock = Lock()


def create_memory_backend(backend_type: str, use_cache: bool = True) -> AbstractMemoryBackend:
    """
    Create a memory backend from a string type.
    
    Args:
        backend_type: One of "local", "s3", "r2"
        use_cache: If True (default), reuse cached backend instances
        
    Returns:
        Configured backend instance
        
    Environment Variables:
        S3 backend:
            - AWS_S3_BUCKET (required)
            - AWS_ACCESS_KEY_ID (optional - uses AWS credential chain)
            - AWS_SECRET_ACCESS_KEY (optional)
            - AWS_REGION or AWS_DEFAULT_REGION (optional, default: us-east-1)
            - AWS_ENDPOINT_URL (optional - for S3-compatible services)
            
        R2 backend:
            - R2_BUCKET_NAME (required)
            - R2_ACCOUNT_ID (required)
            - R2_ACCESS_KEY_ID (required)
            - R2_SECRET_ACCESS_KEY (required)
    """
    backend_type = backend_type.lower().strip()
    
    # Check cache first
    if use_cache:
        with _cache_lock:
            if backend_type in _backend_cache:
                logger.debug(f"Reusing cached {backend_type} memory backend")
                return _backend_cache[backend_type]
    
    # Create new backend
    backend = _create_backend_instance(backend_type)
    
    # Cache it
    if use_cache:
        with _cache_lock:
            _backend_cache[backend_type] = backend
    
    return backend


def _create_backend_instance(backend_type: str) -> AbstractMemoryBackend:
    """Create a new backend instance (internal, no caching)."""
    
    if backend_type == "local":
        logger.info(f"Creating local memory backend at: {LOCAL_MEMORY_BASE_DIR}")
        return LocalMemoryBackend(base_dir=LOCAL_MEMORY_BASE_DIR)
    
    elif backend_type == "s3":
        bucket_name = config("AWS_S3_BUCKET", default=None)
        if not bucket_name:
            raise ValueError(
                "S3 backend requires AWS_S3_BUCKET environment variable"
            )
        
        logger.info(f"Creating S3 memory backend for bucket: {bucket_name}")
        S3MemoryBackend = load_optional(
            "S3 memory backend",
            "s3",
            lambda: __import__(
                "omnicoreagent.core.tools.memory_tool.s3_storage",
                fromlist=["S3MemoryBackend"],
            ).S3MemoryBackend,
        )
        return S3MemoryBackend(
            bucket_name=bucket_name,
            prefix="memories/",
            region=config("AWS_REGION", default=None),
            aws_access_key_id=config("AWS_ACCESS_KEY_ID", default=None),
            aws_secret_access_key=config("AWS_SECRET_ACCESS_KEY", default=None),
            endpoint_url=config("AWS_ENDPOINT_URL", default=None),
        )
    
    elif backend_type == "r2":
        bucket_name = config("R2_BUCKET_NAME", default=None)
        account_id = config("R2_ACCOUNT_ID", default=None)
        access_key_id = config("R2_ACCESS_KEY_ID", default=None)
        secret_access_key = config("R2_SECRET_ACCESS_KEY", default=None)
        
        missing = []
        if not bucket_name:
            missing.append("R2_BUCKET_NAME")
        if not account_id:
            missing.append("R2_ACCOUNT_ID")
        if not access_key_id:
            missing.append("R2_ACCESS_KEY_ID")
        if not secret_access_key:
            missing.append("R2_SECRET_ACCESS_KEY")
        
        if missing:
            raise ValueError(
                f"R2 backend requires environment variables: {', '.join(missing)}"
            )
        
        logger.info(f"Creating R2 memory backend for bucket: {bucket_name}")
        R2MemoryBackend = load_optional(
            "R2 memory backend",
            "s3",
            lambda: __import__(
                "omnicoreagent.core.tools.memory_tool.r2_storage",
                fromlist=["R2MemoryBackend"],
            ).R2MemoryBackend,
        )
        return R2MemoryBackend(
            bucket_name=bucket_name,
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            prefix="memories/",
        )
    
    else:
        raise ValueError(
            f"Unknown backend type: '{backend_type}'. "
            f"Must be one of: local, s3, r2"
        )


def clear_backend_cache():
    """Clear the backend cache (useful for testing or reconfiguration)."""
    with _cache_lock:
        _backend_cache.clear()
        logger.info("Memory backend cache cleared")
