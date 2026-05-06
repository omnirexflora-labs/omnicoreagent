from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader
from omnicoreagent.core.types import AgentState, Message, SessionState, ToolCallResult
from omnicoreagent.core.utils import build_xml_observations_block, logger

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard

TOOL_OUTPUT_OFFLOAD_EXCLUDED_TOOLS = frozenset(
    {
        "read_artifact",
        "tail_artifact",
        "search_artifact",
        "list_artifacts",
        "memory_view",
        "memory_create_update",
    }
)


class ToolObservationHandler:
    """Normalize, protect, format, and persist tool observations."""

    def __init__(
        self,
        agent_name: str,
        tool_offloader: ToolResponseOffloader,
        guardrail: PromptInjectionGuard | None = None,
    ):
        self.agent_name = agent_name
        self.tool_offloader = tool_offloader
        self.guardrail = guardrail

    async def parse(self, raw_output: str | dict[str, Any]) -> dict[str, Any]:
        """
        Normalize tool output into a single observation shape.

        Always returns:
        {
            "status": "success" | "partial" | "error",
            "tools_results": [
                {
                    "tool_name": str,
                    "args": dict | None,
                    "status": "success" | "error",
                    "data": dict | str | None,
                    "message": str | None,
                },
                ...
            ]
        }
        """
        try:
            if isinstance(raw_output, str):
                try:
                    parsed = json.loads(raw_output)
                except json.JSONDecodeError:
                    logger.warning("Tool observation output is not valid JSON.")
                    return {
                        "status": "error",
                        "tools_results": [
                            {
                                "tool_name": "unknown",
                                "args": None,
                                "status": "error",
                                "data": None,
                                "message": raw_output,
                            }
                        ],
                    }
            elif isinstance(raw_output, dict):
                parsed = raw_output
            else:
                return {
                    "status": "error",
                    "tools_results": [
                        {
                            "tool_name": "unknown",
                            "args": None,
                            "status": "error",
                            "data": None,
                            "message": str(raw_output),
                        }
                    ],
                }

            normalized_results = []

            if "tools_results" in parsed:
                raw_results = parsed["tools_results"]
            elif "successes" in parsed or "errors" in parsed:
                raw_results = []
                for success in parsed.get("successes", []):
                    raw_results.append({**success, "status": "success"})
                for error in parsed.get("errors", []):
                    raw_results.append({**error, "status": "error"})
            else:
                raw_results = [parsed]

            for item in raw_results:
                tool_name = item.get("tool_name") or item.get("tool") or "unknown"
                status = item.get("status", "success")
                args = item.get("args")

                data = item.get("data")
                message = item.get("message") or item.get("error")

                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        pass

                normalized_results.append(
                    {
                        "tool_name": tool_name,
                        "args": args,
                        "status": status,
                        "data": data,
                        "message": message,
                    }
                )

            success_count = sum(
                1 for result in normalized_results if result["status"] == "success"
            )
            error_count = sum(
                1 for result in normalized_results if result["status"] == "error"
            )

            if success_count > 0 and error_count == 0:
                global_status = "success"
            elif success_count > 0 and error_count > 0:
                global_status = "partial"
            else:
                global_status = "error"

            return {
                "status": global_status,
                "tools_results": normalized_results,
            }

        except Exception as e:
            logger.error(f"Error parsing tool observation: {e}", exc_info=True)
            return {
                "status": "error",
                "tools_results": [
                    {
                        "tool_name": "unknown",
                        "args": None,
                        "status": "error",
                        "data": None,
                        "message": f"Observation parsing failed: {str(e)}",
                    }
                ],
            }

    def scrub_results(self, tools_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Scrub tool output through guardrails before it enters LLM context."""
        if not self.guardrail:
            return tools_results

        for result in tools_results:
            for field in ("data", "message"):
                content = result.get(field)
                if content is None:
                    continue
                text = str(content) if not isinstance(content, str) else content
                if not text.strip():
                    continue

                check = self.guardrail.check(text)
                if check.threat_level.value in ("dangerous", "critical"):
                    tool_name = result.get("tool_name", "unknown")
                    logger.warning(
                        f"Guardrail blocked tool output from '{tool_name}': "
                        f"{check.threat_level.value} (score: {check.threat_score})"
                    )
                    result[field] = (
                        f"[Tool output blocked by guardrail: {check.message}]"
                    )
                    result["status"] = "error"
                elif check.threat_level.value == "suspicious":
                    tool_name = result.get("tool_name", "unknown")
                    logger.info(
                        f"Guardrail flagged suspicious tool output from '{tool_name}': "
                        f"score={check.threat_score}"
                    )

        return tools_results

    def maybe_offload_result(
        self,
        result: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        tool_name = result.get("tool_name", "unknown_tool")
        data = result.get("data")

        if (
            data is None
            or not self.tool_offloader.config.enabled
            or tool_name in TOOL_OUTPUT_OFFLOAD_EXCLUDED_TOOLS
        ):
            return result

        data_str = data if isinstance(data, str) else str(data)
        if not self.tool_offloader.should_offload(data_str):
            return result

        offloaded = self.tool_offloader.offload(
            tool_name=tool_name,
            response=data_str,
            metadata={"args": result.get("args", {}), "session_id": session_id},
        )
        result["data"] = offloaded.context_message
        return result

    def build_results_observation(
        self,
        tool_call_results: list[ToolCallResult],
        tools_results: list[dict[str, Any]],
        session_state: SessionState,
        session_id: str | None,
    ) -> str:
        obs_lines = []
        success_count = 0
        error_count = 0
        tool_counter = defaultdict(int)
        seen_tools: set[str] = set()

        for result in tools_results[: len(tool_call_results)]:
            result = self.maybe_offload_result(
                result=result,
                session_id=session_id,
            )
            tool_name = result.get("tool_name", "unknown_tool")
            args = result.get("args", {})
            status = result.get("status", "unknown")
            data = result.get("data")
            message = result.get("message", "")

            tool_counter[tool_name] += 1
            tool_call_generated_id = f"{tool_name}#{tool_counter[tool_name]}"
            display_value = data if data is not None else message
            if tool_name not in seen_tools:
                seen_tools.add(tool_name)
                session_state.loop_detector.record_tool_call(
                    str(tool_name),
                    str(args),
                    str(display_value),
                )

            if status == "success":
                obs_lines.append(f"{tool_call_generated_id}: {display_value}")
                success_count += 1
            elif status == "error":
                reason = display_value or "Unknown error occurred."
                obs_lines.append(f"{tool_call_generated_id} ERROR: {reason}")
                error_count += 1
            else:
                obs_lines.append(
                    f"{tool_call_generated_id}: Unexpected status '{status}'"
                )
                error_count += 1

        if success_count == len(tools_results):
            return "\n\n".join(obs_lines)
        if success_count > 0 and error_count > 0:
            return "Partial success:\n" + "\n\n".join(obs_lines)
        if error_count == len(tools_results):
            error_details = "\n\n".join(obs_lines)
            return f"Tool execution failed completely:\n{error_details}"
        return "\n\n".join(obs_lines) or "No valid tool results."

    async def append_observations(
        self,
        tools_results: list[dict[str, Any]],
        session_state: SessionState,
        add_message_to_history: Callable[..., Awaitable[Any]],
        session_id: str | None,
        debug: bool,
    ) -> str:
        scrubbed_results = self.scrub_results(tools_results)
        xml_obs_block = build_xml_observations_block(scrubbed_results)
        session_state.messages.append(
            Message(
                role="user",
                content=xml_obs_block,
            )
        )
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )

        if debug:
            logger.info(
                f"Agent state changed from {session_state.state} to {AgentState.OBSERVING}"
            )
        session_state.state = AgentState.OBSERVING
        return xml_obs_block
