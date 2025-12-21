from collections.abc import Callable
from typing import Any

from omnicoreagent.core.agents.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.utils import logger


# handle subscribe to resource change
async def subscribe_resource(
    sessions: dict[str, dict[str, Any]],
    uri: str,
    available_resources: dict[str, list[str]],
):
    server_name, found = await find_resource_server(uri, available_resources)
    try:
        logger.info(f"Subscribing to {uri} resource on {server_name}")
        # check if the server_name still connected
        if found and sessions[server_name]["connected"]:
            await sessions[server_name]["session"].subscribe_resource(uri)
            logger.info(f"Subscribed to {uri} resource on {server_name}")
        else:
            logger.info(f"{server_name} is not connected")
            return None
    except Exception as e:
        logger.info(f"exception to subscribe to resource: {e}")
        return None

    return f"Subscribed to {uri} resource on {server_name}"


async def unsubscribe_resource(
    sessions: dict[str, dict[str, Any]],
    uri: str,
    available_resources: dict[str, list[str]],
):
    server_name, found = await find_resource_server(uri, available_resources)
    if found and sessions[server_name]["connected"]:
        try:
            await sessions[server_name]["session"].unsubscribe_resource(uri)
            logger.info(f"Unsubscribed from {uri} resource on {server_name}")
        except Exception as e:
            logger.info(f"exception to unsubscribe from resource: {e}")
            return None
    else:
        logger.info(f"{server_name} is not connected")
        return None
    return f"Unsubscribed from {uri} resource on {server_name}"


# list all resources in mcp server
async def list_resources(server_names: list[str], sessions: dict[str, dict[str, Any]]):
    """List all resources"""
    resources = []
    for server_name in server_names:
        if sessions[server_name]["connected"]:
            try:
                resources_response = await sessions[server_name][
                    "session"
                ].list_resources()
                resources.extend(resources_response.resources)
            except Exception:
                logger.info(f"{server_name} Does not support resources")
    return resources


async def find_resource_server(
    uri: str, available_resources: dict[str, list[str]]
) -> tuple[str, bool]:
    """Find which server has the resource

    Returns:
        tuple[str, bool]: (server_name, found)
    """
    for server_name, resources in available_resources.items():
        resource_uris = [str(res.uri) for res in resources]
        if uri in resource_uris:
            return server_name, True
    return "", False


# read a resource from mcp server
async def read_resource(
    uri: str,
    sessions: dict[str, dict[str, Any]],
    available_resources: dict[str, list[str]],
    llm_call: Callable[[list[dict[str, Any]]], dict[str, Any]],
    debug: bool = False,
    request_limit: int = None,
    total_tokens_limit: int = None,
):
    """Read a resource"""
    if debug:
        logger.info(f"Reading resource: {uri}")
    usage_limits = UsageLimits(
        request_limit=request_limit, total_tokens_limit=total_tokens_limit
    )
    usage_limits.check_before_request(usage=usage)
    server_name, found = await find_resource_server(uri, available_resources)
    if not found:
        error_message = f"Resource not found: {uri}"
        logger.error(error_message)
        return error_message
    logger.info(f"Resource found in {server_name}")
    try:
        resource_response = await sessions[server_name]["session"].read_resource(uri)
        if debug:
            logger.info("LLM processing resource")
        llm_response = await llm_call(
            messages=[
                {
                    "role": "system",
                    "content": "Analyze the document content and provide a clear, concise summary that captures all essential information. Focus on key points, main concepts, and critical details that give the user a complete understanding without reading the entire document. Present your summary using bullet points for main ideas followed by a brief paragraph for context when needed. Include any technical terms, specifications, instructions, or warnings that are vital to proper understanding. Do not include phrases like 'here is your summary' or 'in summary' - deliver only the informative content directly.",
                },
                {"role": "user", "content": str(resource_response)},
            ]
        )
        if llm_response:
            if hasattr(llm_response, "usage"):
                request_usage = Usage(
                    requests=1,
                    request_tokens=llm_response.usage.prompt_tokens,
                    response_tokens=llm_response.usage.completion_tokens,
                    total_tokens=llm_response.usage.total_tokens,
                )
                usage.incr(request_usage)
                # Check if we've exceeded token limits
                usage_limits.check_tokens(usage)
                # Show remaining resources
                remaining_tokens = usage_limits.remaining_tokens(usage)
                used_tokens = usage.total_tokens
                used_requests = usage.requests
                remaining_requests = request_limit - used_requests
                session_stats.update(
                    {
                        "used_requests": used_requests,
                        "used_tokens": used_tokens,
                        "remaining_requests": remaining_requests,
                        "remaining_tokens": remaining_tokens,
                        "request_tokens": request_usage.request_tokens,
                        "response_tokens": request_usage.response_tokens,
                        "total_tokens": request_usage.total_tokens,
                    }
                )
                if debug:
                    logger.info(
                        f"API Call Stats - Requests: {used_requests}/{request_limit}, "
                        f"Tokens: {used_tokens}/{usage_limits.total_tokens_limit}, "
                        f"Request Tokens: {request_usage.request_tokens}, "
                        f"Response Tokens: {request_usage.response_tokens}, "
                        f"Total Tokens: {request_usage.total_tokens}, "
                        f"Remaining Requests: {remaining_requests}, "
                        f"Remaining Tokens: {remaining_tokens}"
                    )

            if hasattr(llm_response, "choices"):
                response_content = llm_response.choices[0].message.content
            elif hasattr(llm_response, "message"):
                response_content = llm_response.message
        return response_content
    except UsageLimitExceeded as e:
        error_message = f"Usage limit error: {e}"
        logger.error(error_message)
        return error_message
    except Exception as e:
        error_message = f"Error reading resource: {e}"
        logger.error(error_message)
        return error_message
