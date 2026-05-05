import uuid
from typing import Any, Dict, List, Optional, Union

from omnicoreagent.core.agents.react_agent import ReactAgent
from omnicoreagent.core.types import AgentConfig as ReactAgentConfig
from omnicoreagent.mcp_clients_connection.client import MCPClient
from omnicoreagent.core.llm import LLMConnection
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.omni_agent.config import (
    ModelConfig,
    MCPToolConfig,
    AgentConfig,
    normalize_agent_config,
    normalize_mcp_tools,
    normalize_model_config,
)
from omnicoreagent.omni_agent.prompts.prompt_builder import OmniCoreAgentPromptBuilder
from omnicoreagent.omni_agent.prompts.react_suffix import SYSTEM_SUFFIX
from omnicoreagent.core.events.event_router import EventRouter
from omnicoreagent.core.tools.advance_tools.advanced_tools_use import AdvanceToolsUse
from omnicoreagent.core.utils import logger
from omnicoreagent.core.token_usage import Usage
from omnicoreagent.core.guardrails import (
    PromptInjectionGuard,
    DetectionConfig,
)
from omnicoreagent.core.system_prompts import FAST_CONVERSATION_SUMMARY_PROMPT


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
        model_config: Union[Dict[str, Any], ModelConfig],
        mcp_tools: List[Union[Dict[str, Any], MCPToolConfig]] = None,
        local_tools: Optional[Any] = None,
        sub_agents: Optional[Dict[str, Any]] = None,
        agent_config: Optional[Union[Dict[str, Any], AgentConfig]] = None,
        memory_router: Optional[MemoryRouter] = None,
        event_router: Optional[EventRouter] = None,
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
        self.model_config = normalize_model_config(model_config)
        self.mcp_tools = normalize_mcp_tools(mcp_tools)

        # Handle local_tools: optionally convert list to ToolRegistry
        if isinstance(local_tools, list):
            from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

            registry = ToolRegistry()
            for tool in local_tools:
                registry.register(tool)
            self.local_tools = registry
        else:
            self.local_tools = local_tools

        self.sub_agents = sub_agents
        self.agent_config = normalize_agent_config(name, agent_config)

        self.debug = debug
        self._cumulative_usage = Usage()

        self.memory_router = memory_router
        self.event_router = event_router
        self.prompt_builder = prompt_builder or OmniCoreAgentPromptBuilder(
            SYSTEM_SUFFIX
        )
        self.agent = None
        self.mcp_client = None
        self.llm_connection = None
        self.guardrail = None
        self.guardrail_mode = "full"  # Default: full protection

        self._initialized = False

    async def initialize(self):
        """Initialize the agent resources (memory, config, tools)"""
        if self._initialized:
            return

        if not self.memory_router:
            self.memory_router = MemoryRouter(memory_store_type="in_memory")

        if not self.event_router:
            self.event_router = EventRouter(event_store_type="in_memory")

        agent_cfg = self.agent_config

        # Guardrail mode: "full" (default), "input_only", or "off"
        # "full" = check user input + tool outputs + MCP responses
        # "input_only" = check user input only (legacy behavior)
        # "off" = no guardrail protection
        self.guardrail_mode = agent_cfg.get("guardrail_mode", "full")

        if self.guardrail_mode != "off":
            guardrail_config = agent_cfg.get("guardrail_config", {})
            g_config = DetectionConfig(**guardrail_config)
            self.guardrail = PromptInjectionGuard(g_config)
            logger.info(
                f"Guardrail enabled for agent '{self.name}' "
                f"(mode: {self.guardrail_mode})"
            )
        else:
            logger.info(f"Guardrail disabled for agent '{self.name}'")

        self._create_agent()
        self._initialized = True

    def _create_agent(self):
        """Create the appropriate agent based on configuration"""
        if self.mcp_tools:
            self.mcp_client = MCPClient(
                servers=self.mcp_tools,
                model_config=self.model_config,
                api_key=self.model_config.get("api_key"),
                debug=self.debug,
            )
            self.llm_connection = self.mcp_client.llm_connection
        else:
            self.mcp_client = None
            self.llm_connection = LLMConnection(
                model_config=self.model_config,
                api_key=self.model_config.get("api_key"),
            )

        agent_settings = ReactAgentConfig(**self.agent_config)

        if self.memory_router:
            summary_config = agent_settings.memory_config.get("summary")
            self.memory_router.set_memory_config(
                mode=agent_settings.memory_config["mode"],
                value=agent_settings.memory_config["value"],
                summary_config=summary_config,
                summarize_fn=self._summarize_history
                if summary_config and summary_config.get("enabled")
                else None,
            )

        # Pass guardrail to ReactAgent only in "full" mode
        # In "full" mode, tool outputs and MCP responses are scrubbed
        # In "input_only" mode, only user input is checked (at OmniAgent.run level)
        tool_guardrail = self.guardrail if self.guardrail_mode == "full" else None
        self.agent = ReactAgent(config=agent_settings, guardrail=tool_guardrail)
        if self.local_tools:
            if self.agent.enable_advanced_tool_use:
                advance_tools_manager = AdvanceToolsUse()
                advance_tools_manager.load_and_process_tools(
                    local_tools=self.local_tools
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
            logger.warning("No LLM connection available for summarization")
            return ""

        instruction = FAST_CONVERSATION_SUMMARY_PROMPT
        if max_tokens:
            instruction += f" Keep the summary roughly under {max_tokens} tokens."

        history_text = ""
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            history_text += f"{role}: {content}\n"

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
            if response:
                if hasattr(response, "choices") and response.choices:
                    response = response.choices[0].message.content.strip()
                elif hasattr(response, "message"):
                    response = response.message.content.strip()
                elif hasattr(response, "text"):
                    response = response.text.strip()
                elif hasattr(response, "content"):
                    response = response.content.strip()
                elif isinstance(response, dict) and "choices" in response:
                    response = response["choices"][0]["message"]["content"].strip()
                elif isinstance(response, str):
                    pass
                else:
                    logger.error(
                        f"No valid response content found in LLM response: {type(response)}"
                    )
                    return ""
                return response
            return ""
        except Exception as e:
            logger.error(f"Summarization callback failed: {e}")
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
            if self.agent.enable_advanced_tool_use:
                mcp_tools = self.mcp_client.available_tools if self.mcp_client else {}
                advance_tools_manager = AdvanceToolsUse()

                advance_tools_manager.load_and_process_tools(
                    mcp_tools=mcp_tools,
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
                logger.warning(f"Query blocked by guardrail: {result.message}")
                return {
                    "response": f"I'm sorry, but I cannot process this request due to safety concerns: {result.message}",
                    "session_id": session_id,
                    "agent_name": self.name,
                    "guardrail_result": result.to_dict(),
                }

        if not session_id:
            session_id = self.generate_session_id()

        omni_agent_prompt = self.prompt_builder.build(
            system_instruction=self.system_instruction
        )

        extra_kwargs = {
            "sessions": self.mcp_client.sessions if self.mcp_client else {},
            "mcp_tools": self.mcp_client.available_tools if self.mcp_client else {},
            "local_tools": self.local_tools,
            "session_id": session_id,
            "sub_agents": self.sub_agents,
        }

        response = await self.agent._run(
            system_prompt=omni_agent_prompt,
            query=query,
            llm_connection=self.llm_connection,
            add_message_to_history=self.memory_router.store_message,
            message_history=self.memory_router.get_messages,
            debug=self.debug,
            event_router=self.event_router.append,
            **extra_kwargs,
        )

        if isinstance(response, dict) and "usage" in response:
            self._cumulative_usage.incr(response["usage"])
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
        average_time = (
            self._cumulative_usage.total_time / self._cumulative_usage.requests
            if self._cumulative_usage.requests > 0
            else 0
        )
        return {
            "total_requests": self._cumulative_usage.requests,
            "total_request_tokens": self._cumulative_usage.request_tokens,
            "total_response_tokens": self._cumulative_usage.response_tokens,
            "total_tokens": self._cumulative_usage.total_tokens,
            "total_time": self._cumulative_usage.total_time,
            "average_time": average_time,
        }

    async def list_all_available_tools(self):
        """List all available tools (MCP and local)"""
        if not self._initialized:
            await self.initialize()

        available_tools = []

        if self.mcp_client:
            for _, tools in self.mcp_client.available_tools.items():
                for tool in tools:
                    if isinstance(tool, dict):
                        available_tools.append(
                            {
                                "name": tool.get("name", ""),
                                "description": tool.get("description", ""),
                                "inputSchema": tool.get("inputSchema", {}),
                                "type": "mcp",
                            }
                        )
                    else:
                        available_tools.append(
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "inputSchema": tool.inputSchema,
                                "type": "mcp",
                            }
                        )
        if self.local_tools:
            available_tools.extend(self.local_tools.get_available_tools())
        return available_tools

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
        if self.mcp_client:
            await self.mcp_client.cleanup()

    async def cleanup_mcp_servers(self):
        """Clean up MCP servers without removing the agent and the config"""
        if self.mcp_client:
            await self.mcp_client.cleanup()


class OmniAgent(OmniCoreAgent):
    """
    Deprecated: Use OmniCoreAgent instead.
    """

    def __init__(self, *args, **kwargs):
        import warnings

        warnings.warn(
            "OmniAgent is deprecated and has been renamed to OmniCoreAgent. "
            "Please update your imports and class usage.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)
