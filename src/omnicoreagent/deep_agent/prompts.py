"""
Prompt helpers for DeepAgent.

The agent harness instructions now live in OmniCoreAgent core. This builder only
wraps the caller's domain instruction and the shared ReAct runtime prompt.
"""

from omnicoreagent.core.agents.prompting import REACT_AGENT_PROMPT


class DeepAgentPromptBuilder:
    """
    Builds prompts with a clean structure:

    1. <system_instruction> - caller's domain instruction
    2. {REACT_AGENT_PROMPT} - shared runtime loop and tool protocol
    """

    def __init__(self, react_prompt: str = REACT_AGENT_PROMPT):
        """
        Initialize the prompt builder.

        Args:
            react_prompt: The ReAct runtime prompt (defaults to REACT_AGENT_PROMPT)
        """
        self.react_prompt = react_prompt.strip()

    def build(
        self,
        *,
        system_instruction: str = None,
        user_instruction: str = None,
    ) -> str:
        """
        Build the complete prompt.

        Compatible with OmniCoreAgent's prompt_builder interface.

        Args:
            system_instruction: Alias for user_instruction (OmniCoreAgent compat)
            user_instruction: User's domain-specific instruction

        Returns:
            Complete system prompt with clean structure
        """
        instruction = user_instruction or system_instruction

        if not instruction or not instruction.strip():
            raise ValueError("User instruction is required.")

        return f"""<system_instruction>
{instruction.strip()}
</system_instruction>

{self.react_prompt}
""".strip()

    def build_subagent_prompt(
        self,
        *,
        role: str,
        task: str,
        output_path: str,
    ) -> str:
        """
        Build a focused prompt for subagents.

        Subagents get a simpler prompt: their role, task, output contract, and
        the shared runtime protocol.

        Args:
            role: What this subagent specializes in
            task: Specific task to complete
            output_path: Memory path for writing findings

        Returns:
            Subagent system prompt
        """
        return f"""<system_instruction>
You are a specialized subagent with a focused task.

ROLE: {role}

TASK: {task}

OUTPUT REQUIREMENTS:
- Write your findings to: {output_path}
- Use memory_create_update tool to save your findings
- Be thorough but focused on YOUR specific task only
- Do not duplicate work assigned to other subagents
- Structure your findings clearly with headers

When you have completed your investigation:
1. Save findings to the output_path using memory_create_update
2. Confirm you saved the findings
3. Return a brief summary of what you found
</system_instruction>

<subagent_tool_guidance>
  <critical_rules>
    <rule>You are an agent that interacts with the world through tools.</rule>
    <rule>Consult the available tools registry before calling tools.</rule>
    <rule>Do not invent tool names or parameters.</rule>
    <rule>To save your work, use the memory_create_update tool.</rule>
    <rule>Use the XML tool-call format defined in the ReAct runtime prompt.</rule>
  </critical_rules>
</subagent_tool_guidance>

{self.react_prompt}
""".strip()


def build_deep_agent_prompt(user_instruction: str) -> str:
    """
    Build a complete DeepAgent prompt.

    Args:
        user_instruction: User's domain instruction

    Returns:
        Complete system prompt
    """
    builder = DeepAgentPromptBuilder()
    return builder.build(user_instruction=user_instruction)
