import json
from types import SimpleNamespace

import pytest

from omnicoreagent.core.model_protocol import ToolRequest
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.native_catalog import NativeToolCatalog


@pytest.mark.asyncio
async def test_native_catalog_passes_exact_arguments_into_real_local_tool():
    registry = ToolRegistry()
    captured = []

    @registry.register_tool(
        name="echo",
        inputSchema={
            "type": "object",
            "properties": {"payload": {}},
            "required": ["payload"],
        },
    )
    async def echo(payload):
        captured.append(payload)
        return payload

    payload = {
        "id": "001",
        "bool": "false",
        "text": "hello, world",
        "specs": [{"xml": "<tool_call/>"}],
        "null": None,
    }
    catalog = NativeToolCatalog(local_tools=registry)
    binding, args = catalog.resolve(
        ToolRequest("native_id", "echo", json.dumps({"payload": payload}))
    )
    assert await registry.execute_tool(binding.name, args) == payload
    assert captured == [payload]


def test_colliding_mcp_and_local_names_have_stable_concrete_bindings():
    registry = ToolRegistry()

    @registry.register_tool(name="lookup")
    async def lookup(query: str):
        return query

    tool = SimpleNamespace(
        name="lookup", description="lookup", inputSchema={"type": "object"}
    )
    catalog = NativeToolCatalog(
        local_tools=registry, mcp_tools={"one": [tool], "two": [tool]}
    )
    definitions = catalog.definitions()
    assert len({d["function"]["name"] for d in definitions}) == 3
    identities = set()
    for definition in definitions:
        binding, _ = catalog.resolve(
            ToolRequest("id", definition["function"]["name"], '{"query":"q"}')
        )
        identities.add((binding.provider, binding.server, binding.name))
    assert identities == {
        ("local", None, "lookup"),
        ("mcp", "one", "lookup"),
        ("mcp", "two", "lookup"),
    }
    with pytest.raises(ValueError, match="not available"):
        catalog.resolve(ToolRequest("id", "lookup", "{}"))
    reordered = NativeToolCatalog(
        local_tools=registry, mcp_tools={"two": [tool], "one": [tool]}
    )
    assert set(catalog.bindings) == set(reordered.bindings)


def test_invalid_provider_names_are_mapped_without_losing_identity():
    name = "tool.with invalid/unicode-名" * 4
    catalog = NativeToolCatalog(
        mcp_tools={"server": [{"name": name, "inputSchema": {"type": "object"}}]}
    )
    exposed = catalog.definitions()[0]["function"]["name"]
    assert len(exposed) <= 64
    binding, _ = catalog.resolve(ToolRequest("id", exposed, "{}"))
    assert binding.name == name
    assert binding.server == "server"


def test_advanced_discovery_is_scoped_to_each_catalog_and_unlocks_schemas():
    registry = ToolRegistry()

    @registry.register_tool(
        name="weather", description="Look up weather forecast temperature"
    )
    async def weather(city: str):
        return city

    first = NativeToolCatalog(local_tools=registry, advanced=True)
    second = NativeToolCatalog(local_tools=registry, advanced=True)
    assert first.definitions() == []
    with pytest.raises(ValueError):
        first.resolve(ToolRequest("id", "weather", '{"city":"Lagos"}'))
    found = first.discover("weather forecast temperature")
    assert found == first.definitions()
    assert second.definitions() == []
    assert first.resolve(ToolRequest("id", "weather", '{"city":"Lagos"}'))[1] == {
        "city": "Lagos"
    }


def test_configured_delegation_schema_excludes_runtime_session_id():
    class Child:
        name = "researcher"
        system_instruction = "Research"

        async def run(self, query: str, session_id=None):
            return query

    child = Child()
    catalog = NativeToolCatalog(sub_agents=[child], advanced=True)
    definition = catalog.definitions()[0]["function"]
    assert definition["name"] == "delegate_researcher"
    assert "session_id" not in definition["parameters"]["properties"]
    binding, args = catalog.resolve(
        ToolRequest("child_id", definition["name"], '{"query":"research"}')
    )
    assert binding.agent is child
    assert args == {"query": "research"}
    with pytest.raises(ValueError, match="Invalid arguments"):
        catalog.resolve(
            ToolRequest(
                "child_id", definition["name"], '{"query":"q","session_id":"override"}'
            )
        )


def test_native_schema_inference_describes_nested_arrays_and_nullable_values():
    registry = ToolRegistry()

    @registry.register_tool()
    async def submit(
        items: list[dict[str, str]], enabled: bool, count: int | None = None
    ):
        pass

    schema = NativeToolCatalog(local_tools=registry).definitions()[0]["function"][
        "parameters"
    ]
    assert schema["properties"]["items"] == {
        "type": "array",
        "items": {"type": "object", "additionalProperties": {"type": "string"}},
    }
    assert schema["properties"]["enabled"] == {"type": "boolean"}
    assert schema["properties"]["count"] == {
        "anyOf": [{"type": "integer"}, {"type": "null"}]
    }
    assert schema["required"] == ["items", "enabled"]


def test_catalog_keeps_trusted_workspace_authority():
    registry = ToolRegistry()

    @registry.register_tool(name="read_file")
    async def read_file(path: str):
        return path

    registry.mark_internal_tool_provider("read_file", "workspace")
    catalog = NativeToolCatalog(local_tools=registry, advanced=True)
    binding, args = catalog.resolve(ToolRequest("id", "read_file", '{"path":"a.xml"}'))
    assert binding.provider == "workspace"
    assert args == {"path": "a.xml"}


def test_native_catalog_rejects_wrong_types_before_execution():
    registry = ToolRegistry()

    @registry.register_tool()
    async def charge(amount: int):
        raise AssertionError("validation must not execute tools")

    catalog = NativeToolCatalog(local_tools=registry)
    with pytest.raises(ValueError, match="Invalid arguments"):
        catalog.resolve(ToolRequest("id", "charge", '{"amount":"001"}'))
