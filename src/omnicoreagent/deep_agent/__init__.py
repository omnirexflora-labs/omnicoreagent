"""
DeepAgent Package - compatibility wrapper around OmniCoreAgent harness defaults.

User provides:
- system_instruction: Defines domain
- tools: Defines capabilities

DeepAgent enables:
- OmniCoreAgent dynamic subagent spawning
- Workspace-backed memory by default
- Context management and tool offloading defaults

Prompt Structure:
1. <system_instruction> - User's domain instruction
2. {REACT_AGENT_PROMPT} - ReAct pattern, tools, etc.
"""

from .deep_agent import DeepAgent
from .prompts import (
    DeepAgentPromptBuilder,
    build_deep_agent_prompt,
)
from omnicoreagent.core.subagents import SubagentFactory, build_subagent_tools

__all__ = [
    "DeepAgent",
    "DeepAgentPromptBuilder",
    "build_deep_agent_prompt",
    "SubagentFactory",
    "build_subagent_tools",
]
