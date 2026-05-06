import json
from typing import Any

from omnicoreagent.core.guardrails import PromptInjectionGuard
from omnicoreagent.core.tools.tools_handler import (
    LocalToolHandler,
    MCPToolHandler,
    ToolExecutor,
)
from omnicoreagent.core.types import ParsedResponse, ToolCallResult, ToolError
from omnicoreagent.core.utils import logger, normalize_tool_args


class ToolCallResolver:
    """Resolve model tool-call requests into executable tool calls."""

    def __init__(self, guardrail: PromptInjectionGuard | None = None):
        self.guardrail = guardrail

    def build_sub_agent_tool_error(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> ToolError:
        return ToolError(
            observation=(
                f"INVOCATION ERROR: '{tool_name}' is a sub-agent, not a tool.\n\n"
                f"❌ You used (WRONG):\n"
                f"   <tool_call><tool_name>{tool_name}</tool_name><parameters>...</parameters></tool_call>\n\n"
                f"✓ Must use (CORRECT):\n"
                f"   <agent_call><agent_name>{tool_name}</agent_name><parameters>...</parameters></agent_call>\n\n"
                f"ACTION REQUIRED:\n"
                f"1. Check AVAILABLE SUB AGENT REGISTRY for '{tool_name}' parameter requirements\n"
                f"2. Retry using <agent_call> with <agent_name> tags\n"
                f"3. Ensure parameters match registry definition exactly"
            ),
            tool_name="N/A",
            tool_args=tool_args,
        )

    async def resolve_mcp_tool_action(
        self,
        action: dict[str, Any],
        sessions: dict,
        mcp_tools: dict | None,
    ) -> tuple[bool, ToolExecutor | None, dict[str, Any]]:
        if not mcp_tools:
            return False, None, {}

        tool_name = action.get("tool", "").strip()
        for _server_name, tools in mcp_tools.items():
            for tool in tools:
                if tool.name.lower() != tool_name.lower():
                    continue

                mcp_tool_handler = MCPToolHandler(
                    sessions=sessions,
                    tool_data=json.dumps(action),
                    mcp_tools=mcp_tools,
                    guardrail=self.guardrail,
                )
                tool_executor = ToolExecutor(tool_handler=mcp_tool_handler)
                tool_data = await mcp_tool_handler.validate_tool_call_request(
                    tool_data=json.dumps(action),
                    mcp_tools=mcp_tools,
                )
                return True, tool_executor, tool_data

        return False, None, {}

    async def resolve_local_tool_action(
        self,
        action: dict[str, Any],
        local_tools: Any = None,
    ) -> tuple[ToolExecutor | None, dict[str, Any]]:
        if not local_tools:
            return None, {}

        local_tool_handler = LocalToolHandler(local_tools=local_tools)
        tool_executor = ToolExecutor(tool_handler=local_tool_handler)
        tool_data = await local_tool_handler.validate_tool_call_request(
            tool_data=json.dumps(action),
            local_tools=local_tools,
        )
        return tool_executor, tool_data

    async def resolve_single_action(
        self,
        action: dict[str, Any],
        sessions: dict,
        mcp_tools: dict | None,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | ToolCallResult:
        tool_name = action.get("tool", "").strip()
        tool_args = action.get("parameters", {})

        if sub_agents:
            sub_agent_names = [sub_agent.name for sub_agent in sub_agents]
            if tool_name in sub_agent_names:
                return self.build_sub_agent_tool_error(
                    tool_name=tool_name,
                    tool_args=tool_args,
                )

        if not tool_name:
            return ToolError(
                observation="No tool name provided in the request",
                tool_name="N/A",
                tool_args=tool_args,
            )

        mcp_tool_found, tool_executor, tool_data = await self.resolve_mcp_tool_action(
            action=action,
            sessions=sessions,
            mcp_tools=mcp_tools,
        )

        if not mcp_tool_found and local_tools:
            tool_executor, tool_data = await self.resolve_local_tool_action(
                action=action,
                local_tools=local_tools,
            )

        if not mcp_tool_found and not local_tools:
            return ToolError(
                observation=f"The tool named '{tool_name}' does not exist in the available tools.",
                tool_name=tool_name,
                tool_args=tool_args,
            )

        if not tool_data.get("action"):
            return ToolError(
                observation=tool_data.get("error", "Tool validation failed"),
                tool_name=tool_name,
                tool_args=tool_args,
            )

        return ToolCallResult(
            tool_executor=tool_executor,
            tool_name=tool_data.get("tool_name"),
            tool_args=normalize_tool_args(tool_data.get("tool_args")),
        )

    def parse_actions(
        self, parsed_response: ParsedResponse
    ) -> ToolError | list[dict[str, Any]]:
        if not parsed_response.data:
            return ToolError(
                observation="Invalid tool call request: No data provided",
                tool_name="unknown",
                tool_args={},
            )

        actions = json.loads(parsed_response.data)
        if not isinstance(actions, list):
            actions = [actions]
        return actions

    def mcp_tools_for_action(
        self, action: dict[str, Any], mcp_tools: dict | None
    ) -> dict | None:
        tool_name = action.get("tool", "").strip()
        if tool_name == "tools_retriever":
            return None
        return mcp_tools

    async def resolve(
        self,
        parsed_response: ParsedResponse,
        sessions: dict,
        mcp_tools: dict | None,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | list[ToolCallResult]:
        try:
            actions = self.parse_actions(parsed_response)
            if isinstance(actions, ToolError):
                return actions

            results: list[ToolCallResult] = []

            for action in actions:
                action_mcp_tools = self.mcp_tools_for_action(
                    action=action,
                    mcp_tools=mcp_tools,
                )
                resolved = await self.resolve_single_action(
                    action=action,
                    sessions=sessions,
                    mcp_tools=action_mcp_tools,
                    local_tools=local_tools,
                    sub_agents=sub_agents,
                )
                if isinstance(resolved, ToolError):
                    return resolved
                results.append(resolved)

            return results

        except Exception as e:
            logger.error(f"Error resolving tool call request: {e}")
            return ToolError(observation=str(e), tool_name="unknown", tool_args={})
