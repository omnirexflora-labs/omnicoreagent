from collections.abc import Callable
from typing import Any

from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.agents.types import AgentConfig


class ReactAgent(BaseReactAgent):
    def __init__(self, config: AgentConfig):
        super().__init__(
            agent_name=config.agent_name,
            max_steps=config.max_steps,
            tool_call_timeout=config.tool_call_timeout,
            request_limit=config.request_limit,
            total_tokens_limit=config.total_tokens_limit,
            enable_tools_knowledge_base=config.enable_tools_knowledge_base,
            tools_results_limit=config.tools_results_limit,
            tools_similarity_threshold=config.tools_similarity_threshold,
            memory_tool_backend=config.memory_tool_backend,
        )

    async def _run(
        self,
        system_prompt: str,
        query: str,
        llm_connection: Callable,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        message_history: Callable[[], Any],
        event_router: Callable,
        debug: bool = False,
        **kwargs,
    ):
        response = await self.run(
            system_prompt=system_prompt,
            query=query,
            llm_connection=llm_connection,
            add_message_to_history=add_message_to_history,
            message_history=message_history,
            event_router=event_router,
            debug=debug,
            sessions=kwargs.get("sessions"),
            mcp_tools=kwargs.get("mcp_tools"),
            local_tools=kwargs.get("local_tools"),  # Pass local tools
            session_id=kwargs.get("session_id"),
            sub_agents=kwargs.get("sub_agents"),
        )
        return response
