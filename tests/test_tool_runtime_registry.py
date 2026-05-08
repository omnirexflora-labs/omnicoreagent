from types import SimpleNamespace

import pytest

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.workspace.factory import clear_workspace_files_backend_cache
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_runtime_registry import ToolRuntimeRegistry
from omnicoreagent.core.workspace.config import WorkspaceConfig


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
async def test_prepare_tools_uses_workspace_config_for_workspace_files(
    monkeypatch, tmp_path, internal_registry, offloader
):
    clear_workspace_files_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "runtime-workspace"
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_workspace_files=True,
        workspace_config=WorkspaceConfig(workspace_dir=workspace),
    )

    prepared = await runtime.prepare_tools(local_tools=None)
    result = await prepared.execute_tool(
        "workspace_file_write",
        {"path": "note.txt", "content": "hello", "mode": "create"},
    )

    assert "created" in result.lower()
    assert (workspace / "files" / "note.txt").read_text() == "hello"
    assert runtime.workspace is not None
    assert offloader.storage is runtime.workspace.artifacts

    clear_workspace_files_backend_cache()


@pytest.mark.asyncio
async def test_subagents_enable_workspace_file_tools(
    monkeypatch, tmp_path, internal_registry, offloader
):
    clear_workspace_files_backend_cache()
    monkeypatch.delenv("OMNICOREAGENT_WORKSPACE_DIR", raising=False)
    workspace = tmp_path / "subagent-workspace"
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_subagents=True,
        enable_workspace_files=False,
        workspace_config=WorkspaceConfig(workspace_dir=workspace),
    )

    prepared = await runtime.prepare_tools(local_tools=None)

    tool_names = [tool.name for tool in prepared.list_tools()]
    assert "workspace_file_write" in tool_names
    assert "workspace_file_view" in tool_names
    assert runtime.workspace is not None

    clear_workspace_files_backend_cache()


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
    assert (
        'filters: array of objects ({"field": string, "active": boolean})' in rendered
    )
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


@pytest.mark.asyncio
async def test_advanced_tool_use_keeps_retriever_visible_and_custom_tools_searchable(
    internal_registry, offloader
):
    runtime = make_runtime(
        internal_registry,
        offloader,
        enable_advanced_tool_use=True,
    )
    local_tools = ToolRegistry()

    @local_tools.register_tool(description="Search products in the catalog.")
    def search_products(query: str):
        return {"query": query}

    prepared = await runtime.prepare_tools(local_tools=local_tools)
    rendered = await runtime.render_prompt_registry(local_tools=prepared)

    assert "tools_retriever:" in rendered
    assert "search_products:" not in rendered

    retrieval = await prepared.execute_tool(
        "tools_retriever",
        {
            "query": "search ecommerce product catalog for headphones by product name"
        },
    )

    assert "search_products" in retrieval["data"]
