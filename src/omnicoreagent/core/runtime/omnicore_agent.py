from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from omnicoreagent.core.runtime import agent_runtime


class OmniCoreAgent:
    """
    A simple, user-friendly interface for creating and using MCP agents.

    This class provides a high-level API that abstracts away the complexity
    of MCP client configuration and agent creation.
    """

    def __init__(
        self,
        name: str,
        system_instruction: str,
        model_config: Any,
        mcp_tools: Optional[List[Any]] = None,
        local_tools: Optional[Any] = None,
        sub_agents: Optional[Dict[str, Any]] = None,
        agent_config: Optional[Any] = None,
        memory_router: Optional[Any] = None,
        event_router: Optional[Any] = None,
        prompt_builder: Optional[Any] = None,
        debug: bool = False,
    ):
        """
        Initialize the OmniCoreAgent with user-friendly configuration.

        Args:
            name: Name of the agent
            system_instruction: System instruction for the agent
            model_config: Model configuration (dict or ModelConfig)
            mcp_tools: List of MCP tool configurations (optional)
            local_tools: LocalToolsIntegration instance (optional)
            sub_agents: SubAgentsIntegration instance (optional)
            agent_config: Optional agent configuration
            embedding_config: Optional embedding configuration
            memory_router: Optional memory router (MemoryRouter)
            event_router: Optional event router (EventRouter)
            debug: Enable debug logging
        """
        self.name = name
        self.system_instruction = system_instruction
        self.model_config = agent_runtime.build_model_config(model_config)
        self.mcp_tools = agent_runtime.build_mcp_tools(mcp_tools)
        self.local_tools = agent_runtime.normalize_local_tools(local_tools)

        self.sub_agents = sub_agents
        self.agent_config = agent_runtime.build_agent_config(name, agent_config)

        self.debug = debug
        self._cumulative_usage = None

        self.memory_router = memory_router
        self.event_router = event_router
        if prompt_builder:
            self.prompt_builder = prompt_builder
        else:
            self.prompt_builder = agent_runtime.LazyDefaultPromptBuilder()
        self.agent = None
        self.mcp_client = None
        self.llm_connection = None
        self.guardrail = None
        self._subagent_factory = None
        self.guardrail_mode = "full"  # Default: full protection

        self._initialized = False

    async def initialize(self):
        """Initialize the agent resources (memory, config, tools)"""
        if self._initialized:
            return

        if not self.memory_router:
            self.memory_router = agent_runtime.default_memory_router()

        if not self.event_router:
            self.event_router = agent_runtime.default_event_router()

        agent_cfg = self.agent_config
        self.guardrail_mode, self.guardrail = agent_runtime.build_guardrail(
            self.name, agent_cfg
        )

        self._create_agent()
        self._initialized = True

    def _create_agent(self):
        """Create the appropriate agent based on configuration"""
        self.mcp_client, self.llm_connection = agent_runtime.create_llm_runtime(
            mcp_tools=self.mcp_tools,
            model_config=self.model_config,
            debug=self.debug,
        )

        agent_settings = agent_runtime.build_agent_settings(self.agent_config)
        agent_runtime.configure_memory_router(
            memory_router=self.memory_router,
            agent_settings=agent_settings,
            summarize_fn=self._summarize_history,
        )

        self.agent = agent_runtime.create_react_agent(
            agent_settings=agent_settings,
            guardrail=self.guardrail,
            guardrail_mode=self.guardrail_mode,
        )
        self._prepare_dynamic_subagents()
        if self.local_tools:
            agent_runtime.index_tools_for_advanced_use(
                enabled=self.agent.enable_advanced_tool_use,
                local_tools=self.local_tools,
            )

    def _prepare_dynamic_subagents(self):
        """Register dynamic subagent spawning tools when enabled."""
        self._subagent_factory, self.local_tools = (
            agent_runtime.prepare_dynamic_subagents(
                enabled=self.agent_config.get("enable_subagents", False),
                existing_factory=self._subagent_factory,
                base_model_config=self.model_config,
                mcp_tools=self.mcp_tools,
                local_tools=self.local_tools,
                agent_config=self.agent_config,
                prompt_builder=self.prompt_builder,
                event_router=self.event_router,
                memory_router=self.memory_router,
                debug=self.debug,
            )
        )

    async def _summarize_history(
        self, messages: list[Dict[str, Any]], max_tokens: int = None
    ) -> str:
        """
        Callback for memory router to summarize message history using the agent's LLM.

        Args:
            messages: List of messages to summarize
            max_tokens: Optional token budget hint

        Returns:
            String summary of the messages
        """
        if not self.llm_connection:
            agent_runtime.runtime_logger().warning(
                "No LLM connection available for summarization"
            )
            return ""

        instruction = agent_runtime.summary_instruction(max_tokens)
        history_text = agent_runtime.render_history(messages)

        prompt_messages = [
            {
                "role": "system",
                "content": instruction,
            },
            {
                "role": "user",
                "content": f"Here is the conversation history to summarize:\n\n{history_text}",
            },
        ]

        try:
            response = await self.llm_connection.llm_call(messages=prompt_messages)
            return agent_runtime.extract_summary_text(response)
        except Exception as e:
            agent_runtime.runtime_logger().error(f"Summarization callback failed: {e}")
            return ""

    def generate_session_id(self) -> str:
        """Generate a new session ID for the session"""
        return f"omni_core_agent_{self.name}_{uuid.uuid4().hex[:8]}"

    async def connect_mcp_servers(self):
        """Connect to MCP servers if MCP tools are configured"""
        if not self._initialized:
            await self.initialize()

        if self.mcp_client and self.mcp_tools:
            await self.mcp_client.connect_to_servers()
            agent_runtime.index_tools_for_advanced_use(
                enabled=self.agent.enable_advanced_tool_use,
                mcp_tools=self.mcp_client.available_tools if self.mcp_client else {},
                local_tools=self.local_tools,
            )

    async def run(self, query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Run the agent with a query and optional session ID.

        Args:
            query: The user query
            session_id: Optional session ID for session continuity

        Returns:
            Dict containing response and session_id
        """
        if not self._initialized:
            await self.initialize()

        if self.guardrail:
            result = self.guardrail.check(query)
            if not result.is_safe:
                agent_runtime.runtime_logger().warning(
                    f"Query blocked by guardrail: {result.message}"
                )
                return {
                    "response": f"I'm sorry, but I cannot process this request due to safety concerns: {result.message}",
                    "session_id": session_id,
                    "agent_name": self.name,
                    "guardrail_result": result.to_dict(),
                }

        if not session_id:
            session_id = self.generate_session_id()

        runtime_prompt = self.prompt_builder.build(
            system_instruction=self.system_instruction
        )

        extra_kwargs = {
            "sessions": self.mcp_client.sessions if self.mcp_client else {},
            "mcp_tools": self.mcp_client.available_tools if self.mcp_client else {},
            "local_tools": self.local_tools,
            "session_id": session_id,
            "sub_agents": self.sub_agents,
        }

        response = await self.agent.run(
            system_prompt=runtime_prompt,
            query=query,
            llm_connection=self.llm_connection,
            add_message_to_history=self.memory_router.store_message,
            message_history=self.memory_router.get_messages,
            debug=self.debug,
            event_router=self.event_router.append,
            **extra_kwargs,
        )

        if isinstance(response, dict) and "usage" in response:
            self._usage().incr(response["usage"])
            return {
                "response": response["answer"],
                "session_id": session_id,
                "agent_name": self.name,
                "metric": response["usage"],
            }

        return {"response": response, "session_id": session_id, "agent_name": self.name}

    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get the cumulative metrics for the lifecycle of the agent.

        Returns:
            Dict containing total requests, tokens, and time.
        """
        cumulative_usage = self._usage()
        average_time = (
            cumulative_usage.total_time / cumulative_usage.requests
            if cumulative_usage.requests > 0
            else 0
        )
        return {
            "total_requests": cumulative_usage.requests,
            "total_request_tokens": cumulative_usage.request_tokens,
            "total_response_tokens": cumulative_usage.response_tokens,
            "total_tokens": cumulative_usage.total_tokens,
            "total_time": cumulative_usage.total_time,
            "average_time": average_time,
        }

    def _usage(self):
        if self._cumulative_usage is None:
            self._cumulative_usage = agent_runtime.runtime("Usage")()
        return self._cumulative_usage

    async def list_all_available_tools(self):
        """List all available tools (MCP and local)"""
        if not self._initialized:
            await self.initialize()

        return agent_runtime.available_tools(self.mcp_client, self.local_tools)

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get session history for a specific session ID"""
        if not self.memory_router:
            return []

        return await self.memory_router.get_messages(
            session_id=session_id, agent_name=self.name
        )

    async def clear_session_history(self, session_id: Optional[str] = None):
        """Clear session history for a specific session ID or all history"""
        if not self.memory_router:
            return

        if session_id:
            await self.memory_router.clear_memory(
                session_id=session_id, agent_name=self.name
            )
        else:
            await self.memory_router.clear_memory(agent_name=self.name)

    async def stream_events(self, session_id: str):
        async for event in self.event_router.stream(session_id=session_id):
            yield event

    async def get_events(self, session_id: str):
        return await self.event_router.get_events(session_id=session_id)

    async def get_trace(self, session_id: str) -> Dict[str, Any]:
        trace = await self.event_router.get_trace(session_id=session_id)
        return trace.model_dump()

    async def get_event_store_type(self) -> str:
        """Get the current event store type."""
        return self.event_router.get_event_store_type()

    async def is_event_store_available(self) -> bool:
        """Check if the event store is available."""
        return self.event_router.is_available()

    async def get_event_store_info(self) -> Dict[str, Any]:
        """Get information about the current event store."""
        return self.event_router.get_event_store_info()

    async def switch_event_store(self, event_store_type: str):
        """Switch to a different event store type."""
        self.event_router.switch_event_store(event_store_type)

    async def get_memory_store_type(self) -> str:
        """Get the current memory store type."""
        return self.memory_router.memory_store_type

    async def switch_memory_store(self, memory_store_type: str):
        """Switch to a different memory store type."""
        self.memory_router.switch_memory_store(memory_store_type)

    async def cleanup(self):
        """Clean up resources"""
        if self._subagent_factory:
            await self._subagent_factory.cleanup()
            self._subagent_factory = None
        if self.mcp_client:
            await self.mcp_client.cleanup()

    async def cleanup_mcp_servers(self):
        """Clean up MCP servers without removing the agent and the config"""
        if self.mcp_client:
            await self.mcp_client.cleanup()
