"""Tool routes for OmniServe."""

from fastapi import APIRouter, Request

from ..models import ToolInfo, ToolsResponse
from ..state import get_agent


def create_tools_router() -> APIRouter:
    """Create tool-listing endpoints."""
    router = APIRouter(tags=["Tools"])

    @router.get(
        "/tools",
        response_model=ToolsResponse,
        summary="List available tools",
        description="List all tools available to the agent.",
    )
    async def list_tools(request: Request) -> ToolsResponse:
        agent = get_agent(request)
        tools = await agent.list_all_available_tools()

        tool_infos = [
            ToolInfo(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                inputSchema=tool.get("inputSchema", {}),
                type=tool.get("type", "unknown"),
            )
            for tool in tools
        ]

        return ToolsResponse(tools=tool_infos, total=len(tool_infos))

    return router
