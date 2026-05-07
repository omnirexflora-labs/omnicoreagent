"""Prompt builders and prompt constants for OmniCoreAgent."""

from omnicoreagent.core.system_prompts.base import REACT_AGENT_PROMPT
from omnicoreagent.core.system_prompts.builder import (
    AgentPromptContextBuilder,
    OmniCoreAgentPromptBuilder,
)
from omnicoreagent.core.system_prompts.extensions import (
    agent_skills_additional_prompt,
    artifact_tool_additional_prompt,
    build_subagents_additional_prompt,
    tools_retriever_additional_prompt,
    workspace_files_additional_prompt,
)
from omnicoreagent.core.system_prompts.summaries import (
    FAST_CONVERSATION_SUMMARY_PROMPT,
    SUMMARIZER_MEMORY_CONSTRUCTOR_PROMPT,
)

__all__ = [
    "AgentPromptContextBuilder",
    "FAST_CONVERSATION_SUMMARY_PROMPT",
    "OmniCoreAgentPromptBuilder",
    "REACT_AGENT_PROMPT",
    "SUMMARIZER_MEMORY_CONSTRUCTOR_PROMPT",
    "agent_skills_additional_prompt",
    "artifact_tool_additional_prompt",
    "build_subagents_additional_prompt",
    "tools_retriever_additional_prompt",
    "workspace_files_additional_prompt",
]
