from types import SimpleNamespace

import pytest

from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader
from omnicoreagent.core.tools.memory_tool.factory import clear_backend_cache
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_runtime_registry import ToolRuntimeRegistry
from omnicoreagent.core.workspace_config import WorkspaceConfig


@pytest.fixture
def internal_registry():
    return ToolRegistry()


@pytest.fixture
def offloader(tmp_path):
    return ToolResponseOffloader(
        config={"enabled": False},
        base_dir=str(tmp_path),
    )


def make_runtime(internal_registry, offloader, **kwargs):
    return ToolRuntimeRegistry(
        register_internal_tool=internal_registry,
        tool_offloader=offloader,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_prepare_tools_returns_none_without_any_tool_sources(
    internal_registry, offloader
):
    runtime = make_runtime(internal_registry, offloader)

    assert await runtime.prepare_tools(local_tools=None) is None


@pytest.mark.asyncio
async def test_prepare_tools_uses_internal_registry_when_harness_tools_enabled(
    monkeypatch, internal_registry, offloader
):
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_advanced_tool_use=True,
    )

    async def fake_build_advanced_tools(registry):
        @registry.register_tool(
            name="internal_ping",
            inputSchema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            description="Internal ping.",
        )
        async def internal_ping(value: str):
            return value

        return registry

    monkeypatch.setattr(
        "omnicoreagent.core.tools.tool_runtime_registry.build_tool_registry_advance_tools_use",
        fake_build_advanced_tools,
    )

    prepared = await runtime.prepare_tools(local_tools=None)

    assert prepared is internal_registry
    assert "internal_ping" in [tool.name for tool in prepared.list_tools()]


@pytest.mark.asyncio
async def test_prepare_tools_uses_workspace_config_for_memory_tools(
    monkeypatch, tmp_path, internal_registry, offloader
):
    clear_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "runtime-workspace"
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_workspace_memory=True,
        workspace_config=WorkspaceConfig(workspace_dir=workspace),
    )

    prepared = await runtime.prepare_tools(local_tools=None)
    result = await prepared.execute_tool(
        "memory_create_update",
        {"path": "note.txt", "file_text": "hello", "mode": "create"},
    )

    assert "created" in result.lower()
    assert (workspace / "memories" / "note.txt").read_text() == "hello"

    clear_backend_cache()


@pytest.mark.asyncio
async def test_render_prompt_registry_formats_local_tool_schema(
    internal_registry, offloader
):
    runtime = make_runtime(internal_registry, offloader)
    local_tools = ToolRegistry()

    @local_tools.register_tool(
        name="search_docs",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "filters": {
                    "type": "array",
                    "description": "Filter list.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "active": {"type": "boolean"},
                        },
                    },
                },
            },
            "required": ["query"],
        },
        description="Search documentation.\nUse for docs.",
    )
    async def search_docs(query: str, filters: list | None = None):
        return {"query": query, "filters": filters or []}

    rendered = await runtime.render_prompt_registry(local_tools=local_tools)

    assert "search_docs: Search documentation. Use for docs." in rendered
    assert "query: string (required) - Search query." in rendered
    assert 'filters: array of objects ({"field": string, "active": boolean})' in rendered
    assert 'Example: {"field": "...", "active": true}' in rendered


@pytest.mark.asyncio
async def test_render_prompt_registry_includes_mcp_tools_when_advanced_tools_disabled(
    internal_registry, offloader
):
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_advanced_tool_use=False,
    )
    mcp_tool = SimpleNamespace(
        name="mcp_search",
        description="MCP search.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Query."}},
            "required": ["query"],
        },
    )

    rendered = await runtime.render_prompt_registry(mcp_tools={"server": [mcp_tool]})

    assert "mcp_search: MCP search." in rendered
    assert "query: string (required) - Query." in rendered


@pytest.mark.asyncio
async def test_render_prompt_registry_hides_mcp_tools_when_advanced_tools_enabled(
    internal_registry, offloader
):
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_advanced_tool_use=True,
    )
    mcp_tool = SimpleNamespace(
        name="mcp_search",
        description="MCP search.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Query."}},
            "required": ["query"],
        },
    )

    rendered = await runtime.render_prompt_registry(mcp_tools={"server": [mcp_tool]})

    assert rendered == "No tools available"
