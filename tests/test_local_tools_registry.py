import pytest

from omnicoreagent.core.tools.local_tools_registry import Tool, ToolRegistry


@pytest.mark.asyncio
async def test_explicit_tool_names_are_case_insensitive_for_lookup_and_execution():
    registry = ToolRegistry()

    @registry.register_tool(
        name="CamelTool",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        description="Camel tool.",
    )
    async def camel_tool(value: str):
        return {"status": "success", "data": value}

    assert registry.get_tool("cameltool").name == "CamelTool"
    assert registry.get_tool("CAMELTOOL").name == "CamelTool"
    assert await registry.execute_tool("cameltool", {"value": "runtime"}) == {
        "status": "success",
        "data": "runtime",
    }


def test_register_accepts_tool_wrappers_and_rejects_invalid_values():
    registry = ToolRegistry()

    def ping(value: str):
        return value

    class Wrapper:
        def get_tool(self):
            return Tool(
                name="WrappedTool",
                description="Wrapped.",
                inputSchema={"type": "object", "properties": {}, "required": []},
                function=ping,
            )

    registry.register(Wrapper())

    assert registry.get_tool("wrappedtool").name == "WrappedTool"
    with pytest.raises(TypeError):
        registry.register(object())


def test_merge_registers_tools_with_normalized_lookup_keys():
    first = ToolRegistry()
    second = ToolRegistry()

    @second.register_tool(
        name="MergedTool",
        inputSchema={"type": "object", "properties": {}, "required": []},
        description="Merged.",
    )
    def merged_tool():
        return "merged"

    first.merge(second)

    assert first.get_tool("mergedtool").name == "MergedTool"
    assert first.get_available_tools()[0]["name"] == "MergedTool"
