from typing import Any
from omnicoreagent.core.utils import logger


async def refresh_capabilities(
    sessions: dict[str, Any],
    server_names: list[str],
    available_tools: dict[str, Any],
    available_resources: dict[str, Any],
    available_prompts: dict[str, Any],
    debug: bool,
) -> None:
    """Refresh the capabilities of the server and update system prompt"""
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

        try:
            resources_response = await session.list_resources()
            available_resources[server_name] = (
                resources_response.resources if resources_response else []
            )
        except Exception as e:
            logger.info(f"{server_name} does not support resources: {e}")
            available_resources[server_name] = []

        try:
            prompts_response = await session.list_prompts()
            available_prompts[server_name] = (
                prompts_response.prompts if prompts_response else []
            )
        except Exception as e:
            logger.info(f"{server_name} does not support prompts: {e}")
            available_prompts[server_name] = []

    if debug:
        logger.info(f"Refreshed capabilities for {server_names}")

        for category, data in {
            "Tools": available_tools,
            "Resources": available_resources,
            "Prompts": available_prompts,
        }.items():
            logger.info(f"Available {category.lower()} by server:")
            for server_name, items in data.items():
                logger.info(f"  {server_name}:")
                for item in items:
                    logger.info(f"    - {item.name}")

    if debug:
        logger.info("Updated system prompt with new capabilities")
