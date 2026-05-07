from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from omnicoreagent.core.workspace.artifacts import ToolResponseOffloader
from omnicoreagent.core.tools.tool_observation_formatter import (
    ToolObservationFormatter,
)
from omnicoreagent.core.tools.tool_observation_guardrail import scrub_tool_results
from omnicoreagent.core.tools.tool_observation_parser import parse_tool_observation
from omnicoreagent.core.types import AgentState, Message, SessionState, ToolCallResult
from omnicoreagent.core.utils import build_xml_observations_block, logger

if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard


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
        self.formatter = ToolObservationFormatter(tool_offloader=tool_offloader)

    async def parse(self, raw_output: str | dict[str, Any]) -> dict[str, Any]:
        return parse_tool_observation(raw_output)

    def scrub_results(self, tools_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return scrub_tool_results(tools_results, self.guardrail)

    def maybe_offload_result(
        self,
        result: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        return self.formatter.maybe_offload_result(result, session_id)

    def build_results_observation(
        self,
        tool_call_results: list[ToolCallResult],
        tools_results: list[dict[str, Any]],
        session_state: SessionState,
        session_id: str | None,
    ) -> str:
        return self.formatter.build_results_observation(
            tool_call_results=tool_call_results,
            tools_results=tools_results,
            session_state=session_state,
            session_id=session_id,
        )

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
