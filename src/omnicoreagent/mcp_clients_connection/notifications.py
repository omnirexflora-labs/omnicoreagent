import asyncio
from collections.abc import Callable
from typing import Any

from mcp.types import (
    ProgressNotification,
    PromptListChangedNotification,
    ResourceListChangedNotification,
    ResourceUpdatedNotification,
    ToolListChangedNotification,
)

from omnicoreagent.core.utils import logger


async def handle_notifications(
    sessions: dict[str, dict[str, Any]],
    debug: bool = False,
    server_names: list[str] = None,
    available_tools: dict[str, Any] = None,
    available_resources: dict[str, Any] = None,
    available_prompts: dict[str, Any] = None,
    refresh_capabilities: Callable[[], Any] = None,
):
    """Handle incoming notifications from the server."""
    try:
        for server_name in sessions:
            async for message in sessions[server_name]["session"].incoming_messages:
                logger.debug(f"Received notification from {server_name}: {message}")

                # Common refresh function for capability changes
                async def refresh_capabilities_task():
                    try:
                        logger.info(f"Starting capability refresh for {server_name}")

                        await refresh_capabilities(
                            sessions=sessions,
                            server_names=server_names,
                            available_tools=available_tools,
                            available_resources=available_resources,
                            available_prompts=available_prompts,
                            debug=debug,
                        )
                        logger.info(
                            f"Successfully refreshed capabilities after notification from {server_name}"
                        )
                        # Force flush logs
                        for handler in logger.handlers:
                            handler.flush()
                    except Exception as e:
                        logger.error(
                            f"Failed to refresh capabilities after notification from {server_name}: {str(e)}"
                        )
                        # Force flush logs
                        for handler in logger.handlers:
                            handler.flush()

                try:
                    match message.root:
                        case ResourceUpdatedNotification(params=params):
                            logger.info(
                                f"Resource updated: {params.uri} from {server_name}"
                            )
                            # Create and track the task
                            task = asyncio.create_task(refresh_capabilities_task())
                            # Add a callback to log completion
                            task.add_done_callback(
                                lambda t: logger.debug(
                                    f"Capability refresh task completed for {server_name}"
                                )
                            )

                        case ResourceListChangedNotification(params=params):
                            logger.info(f"Resource list changed from {server_name}")
                            task = asyncio.create_task(refresh_capabilities_task())
                            task.add_done_callback(
                                lambda t: logger.debug(
                                    f"Capability refresh task completed for {server_name}"
                                )
                            )

                        case ToolListChangedNotification(params=params):
                            logger.info(f"Tool list changed from {server_name}")
                            task = asyncio.create_task(refresh_capabilities_task())
                            task.add_done_callback(
                                lambda t: logger.debug(
                                    f"Capability refresh task completed for {server_name}"
                                )
                            )

                        case PromptListChangedNotification(params=params):
                            logger.info(f"Prompt list changed from {server_name}")
                            task = asyncio.create_task(refresh_capabilities_task())
                            task.add_done_callback(
                                lambda t: logger.debug(
                                    f"Capability refresh task completed for {server_name}"
                                )
                            )

                        case ProgressNotification(params=params):
                            progress_percentage = (
                                (params.progress / params.total * 100)
                                if params.total > 0
                                else 0
                            )
                            logger.info(
                                f"Progress from {server_name}: {params.progress}/{params.total} "
                                f"({progress_percentage:.1f}%)"
                            )

                        case _:
                            logger.warning(
                                f"Unhandled notification type from {server_name}: {type(message.root).__name__}"
                            )
                except Exception as e:
                    logger.error(
                        f"Error processing notification from {server_name}: {str(e)}"
                    )
                    continue

    except AttributeError:
        logger.warning(f"No notification received from {server_name}")
    except Exception as e:
        logger.error(f"Fatal error in notification handler: {str(e)}")
    finally:
        # Force flush logs at the end of the handler
        for handler in logger.handlers:
            handler.flush()
