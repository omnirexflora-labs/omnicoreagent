"""
DeepAgent - General-purpose agent with multi-agent orchestration.

DeepAgent = OmniCoreAgent + Multi-Agent Orchestration

Key Features:
1. Custom DeepAgentPromptBuilder for clean prompt structure
2. Subagent spawning tools
3. Full agent_config inheritance (context_management, tool_offload, etc.)
4. Memory-first workflow

Prompt Structure:
1. <system_instruction> - User's domain instruction (pure)
2. <deep_agent_capabilities> - Multi-agent orchestration
3. {REACT_AGENT_PROMPT} - ReAct pattern, tool usage, etc.

NOTE: Task paths are chosen dynamically by the lead agent when spawning subagents.
"""

from typing import Optional, List, Dict, Any
import uuid

from omnicoreagent.agent import OmniCoreAgent
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.utils import logger

from .prompts import DeepAgentPromptBuilder
from .subagent_factory import SubagentFactory, build_subagent_tools


DEFAULT_DEEP_AGENT_CONFIG = {
    "tool_call_timeout": 600,
    "max_steps": 50,
    "memory_tool_backend": "local",
    "memory_config": {
        "mode": "sliding_window",
        "value": 15000,
        "summary": {"enabled": True, "retention_policy": "keep"},
    },
    "context_management": {
        "enabled": True,
        "mode": "token_budget",
        "value": 100000,
        "threshold_percent": 75,
        "strategy": "truncate",
        "preserve_recent": 6,
    },
    "tool_offload": {
        "enabled": True,
        "threshold_tokens": 500,
        "threshold_bytes": 2000,
        "max_preview_tokens": 150,
        "storage_dir": "workspace/artifacts",
    },
}


class DeepAgent:
    """
    General-purpose agent with multi-agent orchestration.

    Like OmniCoreAgent, but with:
    1. Custom DeepAgentPromptBuilder (clean prompt structure)
    2. Subagent spawning tools
    3. Full agent_config benefits (context_management, tool_offload, etc.)
    4. Memory_tool_backend always local (required for orchestration)

    NOTE: Task paths for memory organization are chosen dynamically by the
    lead agent when it spawns subagents - not hardcoded in base prompt.
    """

    def __init__(
        self,
        name: str,
        system_instruction: str,
        model_config: Dict[str, Any],
        mcp_tools: Optional[List[Dict]] = None,
        local_tools: Optional[ToolRegistry] = None,
        sub_agents: Optional[List[Any]] = None,
        memory_router: Optional[MemoryRouter] = None,
        event_router: Optional[EventRouter] = None,
        agent_config: Optional[Dict[str, Any]] = None,
        debug: bool = False,
    ):
        """
        Initialize DeepAgent.

        Args:
            name: Agent name
            system_instruction: What this agent does (defines domain)
            model_config: LLM configuration
            mcp_tools: MCP tools configuration
            local_tools: Additional local tools
            sub_agents: Pre-defined sub-agents
            memory_router: Memory router
            event_router: Event router
            agent_config: Agent configuration (merged with good defaults)
            debug: Enable debug logging
        """
        self.name = name
        self.user_instruction = system_instruction
        self.model_config = model_config
        self.mcp_tools = mcp_tools
        self.user_local_tools = local_tools
        self.sub_agents = sub_agents
        self.memory_router = memory_router or MemoryRouter("in_memory")
        self.event_router = event_router or EventRouter("in_memory")
        self.debug = debug

        self.agent_config = self._build_agent_config(agent_config)

        self._prompt_builder = DeepAgentPromptBuilder()

        self._agent: Optional[OmniCoreAgent] = None
        self._subagent_factory: Optional[SubagentFactory] = None
        self._initialized = False

    def _build_agent_config(
        self, user_config: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Build agent config by merging user config with good defaults.

        User can override most settings, but memory_tool_backend is always "local".
        """
        config = DEFAULT_DEEP_AGENT_CONFIG.copy()

        if user_config:
            for key, value in user_config.items():
                if (
                    isinstance(value, dict)
                    and key in config
                    and isinstance(config[key], dict)
                ):
                    config[key] = {**config[key], **value}
                else:
                    config[key] = value

        # config["memory_tool_backend"] = "local"
        if "memory_tool_backend" not in config:
            config["memory_tool_backend"] = "local"

        return config

    async def initialize(self):
        """
        Initialize the DeepAgent.

        Creates:
        - SubagentFactory for spawning subagents (with full agent_config)
        - Combined tools registry (user tools + subagent tools)
        - Lead OmniCoreAgent with DeepAgentPromptBuilder
        """
        if self._initialized:
            return

        logger.info(f"DeepAgent '{self.name}': Initializing...")

        # Handle user_local_tools: optionally convert list to ToolRegistry
        if isinstance(self.user_local_tools, list):
             registry = ToolRegistry()
             for tool in self.user_local_tools:
                 registry.register(tool)
             self.user_local_tools = registry

        self._subagent_factory = SubagentFactory(
            base_model_config=self.model_config,
            mcp_tools=self.mcp_tools,
            local_tools=self.user_local_tools,
            agent_config=self.agent_config,
            prompt_builder=self._prompt_builder,
            event_router=self.event_router,
            memory_router=self.memory_router,
            debug=self.debug,
        )

        tools = self.user_local_tools or ToolRegistry()
        build_subagent_tools(self._subagent_factory, tools)

        self._agent = OmniCoreAgent(
            name=self.name,
            system_instruction=self.user_instruction,
            model_config=self.model_config,
            memory_router=self.memory_router,
            event_router=self.event_router,
            agent_config=self.agent_config,
            mcp_tools=self.mcp_tools,
            local_tools=tools,
            sub_agents=self.sub_agents,
            prompt_builder=self._prompt_builder,
            debug=self.debug,
        )

        if self.mcp_tools:
            await self._agent.connect_mcp_servers()

        self._initialized = True
        logger.info(f"DeepAgent '{self.name}': Initialization complete")

    async def run(
        self,
        query: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the agent with a query.

        Args:
            query: Task or question
            session_id: Optional session ID

        Returns:
            Agent response
        """
        if not self._initialized:
            await self.initialize()

        session_id = session_id or str(uuid.uuid4())
        return await self._agent.run(query, session_id=session_id)

    async def cleanup(self):
        """Clean up all resources."""
        logger.info(f"DeepAgent '{self.name}': Cleaning up...")

        if self._subagent_factory:
            await self._subagent_factory.cleanup()

        if self._agent:
            await self._agent.cleanup()

        self._initialized = False
        logger.info(f"DeepAgent '{self.name}': Cleanup complete")

    async def connect_mcp_servers(self):
        """Connect MCP servers."""
        if self._agent:
            await self._agent.connect_mcp_servers()

    async def cleanup_mcp_servers(self):
        """Disconnect MCP servers."""
        if self._agent:
            await self._agent.cleanup_mcp_servers()

    async def list_all_available_tools(self):
        """List all available tools."""
        if self._agent:
            return await self._agent.list_all_available_tools()
        return []

    @property
    def is_initialized(self) -> bool:
        """Check if the agent is initialized."""
        return self._initialized

    @property
    def prompt_builder(self) -> DeepAgentPromptBuilder:
        """Get the prompt builder."""
        return self._prompt_builder

    # =========================================================================
    # OmniCoreAgent Protocol Compatibility Methods (for OmniServe)
    # =========================================================================

    def generate_session_id(self) -> str:
        """Generate a new session ID for the session."""
        return f"deep_agent_{self.name}_{uuid.uuid4().hex[:8]}"

    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get the cumulative metrics for the lifecycle of the agent.

        Returns:
            Dict containing total requests, tokens, and time.
        """
        if self._agent:
            return await self._agent.get_metrics()
        return {
            "total_requests": 0,
            "total_request_tokens": 0,
            "total_response_tokens": 0,
            "total_tokens": 0,
            "total_time": 0,
            "average_time": 0,
        }

    async def stream_events(self, session_id: str):
        """
        Stream events for a session.

        Args:
            session_id: The session ID to stream events for

        Yields:
            Event objects as they are emitted
        """
        async for event in self.event_router.stream(session_id=session_id):
            yield event

    async def get_events(self, session_id: str):
        """
        Get all events for a session.

        Args:
            session_id: The session ID to get events for

        Returns:
            List of events
        """
        return await self.event_router.get_events(session_id=session_id)

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get session message history for a specific session ID.

        Args:
            session_id: The session ID to get history for

        Returns:
            List of message dictionaries
        """
        return await self.memory_router.get_messages(
            session_id=session_id, agent_name=self.name
        )

    async def clear_session_history(self, session_id: Optional[str] = None):
        """
        Clear session history for a specific session ID or all history.

        Args:
            session_id: Optional session ID. If None, clears all history.
        """
        if session_id:
            await self.memory_router.clear_memory(
                session_id=session_id, agent_name=self.name
            )
        else:
            await self.memory_router.clear_memory(agent_name=self.name)

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
