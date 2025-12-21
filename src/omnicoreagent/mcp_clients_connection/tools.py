from typing import Any

from omnicoreagent.core.utils import logger


async def list_tools(server_names: list[str], sessions: dict[str, dict[str, Any]]):
    """List all tools"""
    try:
        tools = []
        for server_name in server_names:
            if sessions[server_name]["connected"]:
                try:
                    tools_response = await sessions[server_name]["session"].list_tools()
                    tools.extend(tools_response.tools)
                except Exception:
                    logger.info(f"{server_name} Does not support tools")
        return tools
    except Exception as e:
        logger.info(f"error listing tools: {e}")
        return e
