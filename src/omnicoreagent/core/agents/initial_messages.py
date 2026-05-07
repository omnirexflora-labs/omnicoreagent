from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from omnicoreagent.core.types import Message, SessionState
from omnicoreagent.core.logging import logger


class AgentInitialMessagePreparer:
    def __init__(
        self,
        *,
        tool_runtime_registry: Any,
        message_history_loader: Any,
        prompt_context_builder: Any,
        timeout_seconds: float = 20.0,
    ):
        self.tool_runtime_registry = tool_runtime_registry
        self.message_history_loader = message_history_loader
        self.prompt_context_builder = prompt_context_builder
        self.timeout_seconds = timeout_seconds

    async def prepare(
        self,
        *,
        session_state: SessionState,
        system_prompt: str,
        session_id: str,
        message_history: Callable[[], Any],
        mcp_tools: dict | None = None,
        local_tools: Any = None,
        sub_agents: list | None = None,
    ) -> None:
        tools_task = self.tool_runtime_registry.render_prompt_registry(
            mcp_tools=mcp_tools, local_tools=local_tools
        )
        history_task = self.message_history_loader.load(
            message_history=message_history,
            session_id=session_id,
            session_state=session_state,
        )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    tools_task,
                    history_task,
                    return_exceptions=True,
                ),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout during initial message preparation "
                f"({self.timeout_seconds:g}s). Proceeding with defaults."
            )
            results = ["No tools available", None]

        for result in results:
            if isinstance(result, BaseException):
                logger.error(f"prepare_initial_messages error: {result}", exc_info=True)

        tools_section = (
            results[0]
            if not isinstance(results[0], BaseException)
            else "No tools available"
        )

        updated_system_prompt = await self.prompt_context_builder.build_system_prompt(
            base_system_prompt=system_prompt,
            tools_section=tools_section,
            sub_agents=sub_agents,
        )

        session_state.messages.insert(
            0, Message(role="system", content=updated_system_prompt)
        )
        self.prompt_context_builder.inject_current_datetime(session_state.messages)
