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
    return ToolResponseOffloader(config={"enabled": False}, base_dir=str(tmp_path))


def make_runtime(internal_registry, offloader, **kwargs):
    return ToolRuntimeRegistry(
        register_internal_tool=internal_registry, tool_offloader=offloader, **kwargs
    )


@pytest.mark.asyncio
async def test_prepare_tools_returns_none_without_any_tool_sources(
    internal_registry, offloader
):
    runtime = make_runtime(internal_registry, offloader)
    assert await runtime.prepare_tools(local_tools=None) is None


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
        "write_file", {"path": "note.txt", "content": "hello", "mode": "create"}
    )
    assert "created" in result.lower()
    assert (workspace / "files" / "note.txt").read_text() == "hello"
    assert runtime.workspace is not None
    assert offloader.storage is runtime.workspace.artifacts
    clear_workspace_files_backend_cache()


@pytest.mark.asyncio
async def test_subagents_enable_workspace_command_tools(
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
    assert "write_file" in tool_names
    assert "read_file" in tool_names
    assert "grep" in tool_names
    assert runtime.workspace is not None
    clear_workspace_files_backend_cache()


@pytest.mark.asyncio
async def test_workspace_command_tool_names_are_reserved(internal_registry, offloader):
    local_tools = ToolRegistry()

    @local_tools.register_tool(name="grep", description="App grep")
    def app_grep(path: str) -> str:
        return path

    runtime = make_runtime(internal_registry, offloader, enable_workspace_files=True)
    with pytest.raises(ValueError, match="reserve these names"):
        await runtime.prepare_tools(local_tools=local_tools)
    tool_names = [tool.name for tool in local_tools.list_tools()]
    assert tool_names == ["grep"]
    assert runtime.workspace is None
    assert offloader._storage is None
    assert offloader._workspace is None
