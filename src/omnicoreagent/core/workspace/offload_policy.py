from __future__ import annotations

ARTIFACT_ACCESS_TOOLS = frozenset(
    {
        "read_artifact",
        "tail_artifact",
        "search_artifact",
        "list_artifacts",
    }
)

WORKSPACE_FILE_TOOLS = frozenset(
    {
        "workspace_file_view",
        "workspace_file_write",
        "workspace_file_replace",
        "workspace_file_insert",
        "workspace_file_delete",
        "workspace_file_rename",
        "workspace_file_clear",
    }
)

WORKSPACE_ACCESS_TOOLS = frozenset(
    {
        *ARTIFACT_ACCESS_TOOLS,
        *WORKSPACE_FILE_TOOLS,
    }
)


def should_keep_tool_output_inline(tool_name: str | None) -> bool:
    return (tool_name or "") in WORKSPACE_ACCESS_TOOLS
