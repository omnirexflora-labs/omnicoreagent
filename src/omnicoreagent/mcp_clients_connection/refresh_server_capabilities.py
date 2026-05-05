from typing import Any
from omnicoreagent.core.utils import logger


async def refresh_capabilities(
    sessions: dict[str, Any],
    server_names: list[str],
    available_tools: dict[str, Any],
    debug: bool,
) -> None:
    """Refresh connected MCP tool capabilities."""
    for server_name in server_names:
        if not sessions.get(server_name, {}).get("connected", False):
            raise ValueError(f"Not connected to server: {server_name}")

        session = sessions[server_name].get("session")
        if not session:
            logger.warning(f"No session found for server: {server_name}")
            continue

        try:
            tools_response = await session.list_tools()
            available_tools[server_name] = (
                tools_response.tools if tools_response else []
            )
        except Exception as e:
            logger.info(f"{server_name} does not support tools: {e}")
            available_tools[server_name] = []

    if debug:
        logger.info(f"Refreshed capabilities for {server_names}")
        logger.info("Available tools by server:")
        for server_name, items in available_tools.items():
            logger.info(f"  {server_name}:")
            for item in items:
                logger.info(f"    - {item.name}")
