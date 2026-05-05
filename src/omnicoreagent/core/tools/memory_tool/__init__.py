"""
Memory Tool backends for OmniCoreAgent.

Provides pluggable storage backends for the memory tool:
- LocalMemoryBackend: Filesystem-based storage
- S3MemoryBackend: AWS S3 storage
- R2MemoryBackend: Cloudflare R2 storage

Usage:
    # In agent config
    agent_config = {
        "memory_tool_backend": "local",  # or "s3" or "r2"
    }
"""

from omnicoreagent.core.tools.memory_tool.base import AbstractMemoryBackend
from omnicoreagent.core.tools.memory_tool.local_storage import LocalMemoryBackend
from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend
from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool, build_tool_registry_memory_tool

__all__ = [
    # Base class
    "AbstractMemoryBackend",
    # Backends
    "LocalMemoryBackend",
    "S3MemoryBackend",
    "R2MemoryBackend",
    # Factory
    "create_memory_backend",
    # Memory tool
    "MemoryTool",
    "build_tool_registry_memory_tool",
]

_OPTIONAL_EXPORTS = {
    "S3MemoryBackend": "omnicoreagent.core.tools.memory_tool.s3_storage",
    "R2MemoryBackend": "omnicoreagent.core.tools.memory_tool.r2_storage",
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from omnicoreagent._optional import load_optional

    return load_optional(
        name,
        "s3",
        lambda: getattr(__import__(_OPTIONAL_EXPORTS[name], fromlist=[name]), name),
    )
