"""
DeepAgent Package - General-purpose agent with multi-agent orchestration.

DeepAgent = OmniCoreAgent + Multi-Agent Orchestration

User provides:
- system_instruction: Defines domain
- tools: Defines capabilities

DeepAgent adds:
- DeepAgentPromptBuilder with clean structure
- Subagent spawning tools
- Memory-first workflow

Prompt Structure:
1. <system_instruction> - User's domain instruction
2. <deep_agent_capabilities> - Orchestration capabilities
3. {SYSTEM_SUFFIX} - ReAct pattern, tools, etc.
"""

from .deep_agent import DeepAgent
from .prompts import (
    DeepAgentPromptBuilder,
    DEEP_AGENT_ORCHESTRATION_PROMPT,
    build_deep_agent_prompt,
)
from .subagent_factory import SubagentFactory, build_subagent_tools

__all__ = [
    "DeepAgent",
    "DeepAgentPromptBuilder",
    "DEEP_AGENT_ORCHESTRATION_PROMPT",
    "build_deep_agent_prompt",
    "SubagentFactory",
    "build_subagent_tools",
]
