from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tools.base_tool_handler import BaseToolHandler

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
        if hasattr(result, "content") and isinstance(result.content, list):
            texts = [
                getattr(item, "text", None)
                for item in result.content
                if hasattr(item, "text")
            ]
            text = " ".join(t for t in texts if t)
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
            }

        return result
