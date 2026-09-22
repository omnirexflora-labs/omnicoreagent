"""
Core Tools Package.

Exports are resolved lazily so local tool registration does not import advanced
tool retrieval/runtime modules.
"""

from importlib import import_module
from typing import Any

__all__ = ["ToolRegistry", "Tool"]

_EXPORTS = {
    "ToolRegistry": ("omnicoreagent.core.tools.local_tools_registry", "ToolRegistry"),
    "Tool": ("omnicoreagent.core.tools.local_tools_registry", "Tool"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
