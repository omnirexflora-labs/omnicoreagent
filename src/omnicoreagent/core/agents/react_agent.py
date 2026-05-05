from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.guardrails import PromptInjectionGuard
from omnicoreagent.core.types import AgentConfig


class ReactAgent(BaseReactAgent):
    def __init__(
        self, config: AgentConfig, guardrail: PromptInjectionGuard | None = None
    ):
        super().__init__(
            agent_name=config.agent_name,
            max_steps=config.max_steps,
            tool_call_timeout=config.tool_call_timeout,
            request_limit=config.request_limit,
            total_tokens_limit=config.total_tokens_limit,
            enable_advanced_tool_use=config.enable_advanced_tool_use,
            memory_tool_backend=config.memory_tool_backend,
            enable_agent_skills=config.enable_agent_skills,
            context_management_config=config.context_management,
            tool_offload_config=getattr(config, "tool_offload", None),
            guardrail=guardrail,
        )
