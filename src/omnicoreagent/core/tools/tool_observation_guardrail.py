from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omnicoreagent.core.utils import logger

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard


def scrub_tool_results(
    tools_results: list[dict[str, Any]],
    guardrail: PromptInjectionGuard | None,
) -> list[dict[str, Any]]:
    """Scrub tool output through guardrails before it enters LLM context."""
    if not guardrail:
        return tools_results

    for result in tools_results:
        for field in ("data", "message"):
            content = result.get(field)
            if content is None:
                continue
            text = str(content) if not isinstance(content, str) else content
            if not text.strip():
                continue

            check = guardrail.check(text)
            if check.threat_level.value in ("dangerous", "critical"):
                tool_name = result.get("tool_name", "unknown")
                logger.warning(
                    f"Guardrail blocked tool output from '{tool_name}': "
                    f"{check.threat_level.value} (score: {check.threat_score})"
                )
                result[field] = f"[Tool output blocked by guardrail: {check.message}]"
                result["status"] = "error"
            elif check.threat_level.value == "suspicious":
                tool_name = result.get("tool_name", "unknown")
                logger.info(
                    f"Guardrail flagged suspicious tool output from '{tool_name}': "
                    f"score={check.threat_score}"
                )

    return tools_results
