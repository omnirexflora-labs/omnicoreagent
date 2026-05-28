from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tools.local_tool_handler import LocalToolHandler
from omnicoreagent.core.tools.mcp_tool_handler import MCPToolHandler
from omnicoreagent.core.tools.tool_action import ToolAction, parse_tool_actions
from omnicoreagent.core.tools.tool_catalog import find_local_tool_name, find_mcp_tool
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.core.types import ParsedResponse, ToolCallResult, ToolError
from omnicoreagent.core.logging import logger
from omnicoreagent.core.tools.arguments import normalize_tool_args

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard

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

    def resolve_mcp_tool_action(
        self,
        action: ToolAction,
        sessions: dict,
        mcp_tools: dict | None,
    ) -> ToolCallResult | ToolError | None:
        try:
            match = find_mcp_tool(tool_name=action.tool_name, mcp_tools=mcp_tools)
        except ValueError as exc:
            return ToolError(
                observation=str(exc),
                tool_name=action.tool_name,
                tool_args=action.parameters,
            )
        if not match:
            return None

        mcp_tool_handler = MCPToolHandler(
            sessions=sessions,
            server_name=match.server_name,
            guardrail=self.guardrail,
        )
        return ToolCallResult(
            tool_executor=ToolExecutor(tool_handler=mcp_tool_handler),
            tool_name=match.tool_name,
            tool_args=normalize_tool_args(action.parameters),
            tool_provider="mcp",
            tool_server=match.server_name,
        )

    def resolve_local_tool_action(
        self,
        action: ToolAction,
        local_tools: Any = None,
    ) -> ToolCallResult | None:
        local_tool_name = find_local_tool_name(
            tool_name=action.tool_name,
            local_tools=local_tools,
        )
        if not local_tool_name:
            return None

        tool_provider = "local"
        if hasattr(local_tools, "get_tool_provider"):
            provider = local_tools.get_tool_provider(local_tool_name)
            if provider in {"workspace", "artifact"}:
                tool_provider = provider

        local_tool_handler = LocalToolHandler(local_tools=local_tools)
        return ToolCallResult(
            tool_executor=ToolExecutor(tool_handler=local_tool_handler),
            tool_name=local_tool_name,
            tool_args=normalize_tool_args(action.parameters),
            tool_provider=tool_provider,
        )

    async def resolve_single_action(
        self,
        action: ToolAction,
        sessions: dict,
        mcp_tools: dict | None,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | ToolCallResult:
        if sub_agents:
            sub_agent_names = [sub_agent.name for sub_agent in sub_agents]
            if action.tool_name in sub_agent_names:
                return self.build_sub_agent_tool_error(
                    tool_name=action.tool_name,
                    tool_args=action.parameters,
                )

        resolved = None
        if not action.uses_tools_retriever:
            resolved = self.resolve_mcp_tool_action(
                action=action,
                sessions=sessions,
                mcp_tools=mcp_tools,
            )
            if isinstance(resolved, ToolError):
                return resolved

        if not resolved:
            resolved = self.resolve_local_tool_action(
                action=action,
                local_tools=local_tools,
            )

        if resolved:
            return resolved

        return ToolError(
            observation=f"The tool named '{action.tool_name}' does not exist in the available tools.",
            tool_name=action.tool_name,
            tool_args=action.parameters,
        )

    def parse_actions(
        self, parsed_response: ParsedResponse
    ) -> ToolError | list[ToolAction]:
        return parse_tool_actions(parsed_response)

    def mcp_tools_for_action(
        self, action: ToolAction | dict[str, Any], mcp_tools: dict | None
    ) -> dict | None:
        tool_name = (
            action.tool_name
            if isinstance(action, ToolAction)
            else str(action.get("tool", "")).strip()
        )
        if tool_name.lower() == "tools_retriever":
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
