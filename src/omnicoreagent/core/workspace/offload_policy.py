from __future__ import annotations

INLINE_TOOL_PROVIDERS = frozenset({"workspace", "artifact"})


def should_keep_tool_output_inline(tool_provider: str | None) -> bool:
    return (tool_provider or "") in INLINE_TOOL_PROVIDERS
