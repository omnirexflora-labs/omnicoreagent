from types import SimpleNamespace

import pytest

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_prompt_renderer import ToolPromptRenderer


@pytest.mark.asyncio
async def test_render_local_tool_schema():
    registry = ToolRegistry()

    @registry.register_tool(
        name="write_note",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "items": {
                    "type": "array",
                    "description": "Todo items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "done": {"type": "boolean"},
                        },
                    },
                },
            },
            "required": ["title"],
        },
        description="Write a note.",
    )
    async def write_note(title: str, items: list):
        return {"title": title, "items": items}

    rendered = await ToolPromptRenderer().render(local_tools=registry)

    assert "write_note: Write a note." in rendered
    assert "title: string (required) - Note title" in rendered
    assert 'array of objects ({"text": string, "done": boolean})' in rendered
    assert 'Example: {"text": "...", "done": true}' in rendered


@pytest.mark.asyncio
async def test_render_can_hide_mcp_tools():
    mcp_tool = SimpleNamespace(
        name="search",
        description="Search docs.",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    hidden = await ToolPromptRenderer(include_mcp_tools=False).render(
        mcp_tools={"server": [mcp_tool]}
    )
    visible = await ToolPromptRenderer(include_mcp_tools=True).render(
        mcp_tools={"server": [mcp_tool]}
    )

    assert hidden == "No tools available"
    assert "search: Search docs." in visible
