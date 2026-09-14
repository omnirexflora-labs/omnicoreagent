from __future__ import annotations

import asyncio
from typing import Any

from omnicoreagent.core.types import Message, SessionState


class AgentInitialMessagePreparer:
    """Load history and derive prompt capabilities from the actual native catalog."""

    def __init__(
        self,
        *,
        message_history_loader: Any,
        prompt_context_builder: Any,
        timeout_seconds: float = 20.0,
    ):
        self.message_history_loader = message_history_loader
        self.prompt_context_builder = prompt_context_builder
        self.timeout_seconds = timeout_seconds

    async def prepare(
        self,
        *,
        session_state: SessionState,
        system_prompt: str,
        session_id: str,
        message_history,
        catalog,
    ) -> None:
        # A storage failure must not silently start a fresh conversation.
        await asyncio.wait_for(
            self.message_history_loader.load(
                message_history=message_history,
                session_id=session_id,
                session_state=session_state,
            ),
            timeout=self.timeout_seconds,
        )
        bindings = list(catalog.bindings.values())
        capabilities = {
            binding.name for binding in bindings if binding.provider != "mcp"
        }
        updated_system_prompt = await self.prompt_context_builder.build_system_prompt(
            base_system_prompt=system_prompt,
            available_tools=capabilities,
            tool_aliases={
                binding.name: binding.exposed_name
                for binding in bindings
                if binding.provider != "mcp"
            },
            sub_agents=[
                binding.agent for binding in bindings if binding.agent is not None
            ],
        )
        session_state.messages.insert(
            0, Message(role="system", content=updated_system_prompt)
        )
