from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler
from omnicoreagent.core.tools.mcp_results import is_mcp_call_result, mcp_result_text

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard

logger = logging.getLogger(__name__)


class MCPToolHandler(BaseToolHandler):
    """Execute tools from one resolved MCP server session."""

    def __init__(
        self,
        sessions: dict[str, Any],
        server_name: str,
        guardrail: PromptInjectionGuard | None = None,
    ):
        self.sessions = sessions
        self.server_name = server_name
        self.guardrail = guardrail

    async def call(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        try:
            session = self.sessions[self.server_name]["session"]
        except KeyError as exc:
            raise ValueError(
                f"MCP server '{self.server_name}' is not connected."
            ) from exc

        result = await session.call_tool(tool_name, tool_args)
        return self._scrub_mcp_result(tool_name, result)

    def _scrub_mcp_result(self, tool_name: str, result: Any) -> Any:
        """Scrub MCP tool result through guardrails at the client boundary."""
        if not self.guardrail:
            return result

        text = None
        if is_mcp_call_result(result):
            # Structured content reaches the model too, so it is checked.
            text = mcp_result_text(result)
        elif isinstance(result, dict):
            text = str(result.get("data") or result.get("message") or "")
        elif isinstance(result, str):
            text = result

        if not text or not text.strip():
            return result

        check = self.guardrail.check(text)
        if check.threat_level.value in ("dangerous", "critical"):
            logger.warning(
                f"Guardrail blocked MCP response from '{tool_name}' on "
                f"server '{self.server_name}': {check.threat_level.value} "
                f"(score: {check.threat_score})"
            )
            return {
                "status": "error",
                "data": None,
                "message": f"[MCP response blocked by guardrail: {check.message}]",
                "_guardrail_telemetry": {
                    "source": "mcp_tool_output",
                    "tool_name": tool_name,
                    "field": "content",
                    "action": "blocked",
                    "threat_level": check.threat_level.value,
                    "threat_score": check.threat_score,
                    "input_hash": check.input_hash,
                    "message": check.message,
                },
            }

        return result
