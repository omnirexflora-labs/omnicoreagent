from __future__ import annotations

from typing import TYPE_CHECKING, Any

from omnicoreagent.core.logging import logger

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard


def _suspicious_output_action(guardrail: Any) -> str:
    """Read the configured output policy, defaulting safely for adapters/mocks."""
    action = getattr(guardrail, "suspicious_output_action", None)
    if action in {"block", "flag"}:
        return action
    config = getattr(guardrail, "config", None)
    action = getattr(config, "suspicious_output_action", None)
    return action if action in {"block", "flag"} else "flag"


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
            signal = {
                "source": "tool_output",
                "tool_name": result.get("tool_name", "unknown_tool"),
                "field": field,
                "threat_level": check.threat_level.value,
                "threat_score": check.threat_score,
                "input_hash": check.input_hash,
                "message": check.message,
            }
            if check.threat_level.value in ("dangerous", "critical"):
                tool_name = result.get("tool_name", "unknown")
                logger.warning(
                    f"Guardrail blocked tool output from '{tool_name}': "
                    f"{check.threat_level.value} (score: {check.threat_score})"
                )
                result[field] = f"[Tool output blocked by guardrail: {check.message}]"
                result["status"] = "error"
                signal["action"] = "blocked"
                signal["policy"] = "threat_level"
            elif check.threat_level.value == "suspicious":
                tool_name = result.get("tool_name", "unknown")
                action = _suspicious_output_action(guardrail)
                if action == "block":
                    logger.warning(
                        f"Guardrail blocked suspicious tool output from '{tool_name}': "
                        f"score={check.threat_score}"
                    )
                    result[field] = (
                        f"[Tool output blocked by guardrail: {check.message}]"
                    )
                    result["status"] = "error"
                    signal["action"] = "blocked"
                    signal["policy"] = "suspicious_output"
                else:
                    logger.info(
                        f"Guardrail flagged suspicious tool output from '{tool_name}': "
                        f"score={check.threat_score}"
                    )
                    signal["action"] = "flagged"
                    signal["policy"] = "suspicious_output"
            if signal.get("action"):
                result["_guardrail_telemetry"] = signal

    return tools_results
