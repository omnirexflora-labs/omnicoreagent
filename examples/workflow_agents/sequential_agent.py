from omnicoreagent import (
    OmniCoreAgent,
    MemoryRouter,
    EventRouter,
    SequentialAgent,
    ToolRegistry,
    logger,
)

# using low level import
# from omnicoreagent.omni_agent.workflow.sequential_agent import SequentialAgent
import asyncio


def build_tool_registry_system_monitor_agent() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("system_info")
    def system_info() -> str:
        """Get basic system information of the server"""
        import platform
        import time

        return (
            "System Information:\n"
            f"• OS: {platform.system()} {platform.release()}\n"
            f"• Architecture: {platform.machine()}\n"
            f"• Python Version: {platform.python_version()}\n"
            f"• Current Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return registry


system_monitor_agent = OmniCoreAgent(
    name="SystemMonitorAgent",
    system_instruction=(
        "You are a System Monitor Agent. Your responsibility is to retrieve the current "
        "system information using the `system_info` tool. Only use the tool to get the data; do not guess. "
        "Include OS, architecture, Python version, and current time."
    ),
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    # embedding_config={"provider": "voyage", "model": "voyage-3.5", "dimensions": 1024, "encoding_format": "base64"},
    local_tools=build_tool_registry_system_monitor_agent(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)


def build_tool_registry_text_formatter_agent() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("format_text")
    def format_text(text: str, style: str = "normal") -> str:
        """Format text in various styles"""
        if style == "uppercase":
            return text.upper()
        if style == "lowercase":
            return text.lower()
        if style == "title":
            return text.title()
        if style == "reverse":
            return text[::-1]
        return text

    return registry


text_formatter_agent = OmniCoreAgent(
    name="TextFormatterAgent",
    system_instruction=(
        "You are a Text Formatting Agent. Your task is to take the input string "
        "and format it to uppercase. Do not add any extra text or explanation."
    ),
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    # embedding_config={"provider": "voyage", "model": "voyage-3.5", "dimensions": 1024, "encoding_format": "base64"},
    local_tools=build_tool_registry_text_formatter_agent(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)

FILE_SYSTEM_MCP_TOOLS = [
    {
        "name": "filesystem",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/home/abiorh/Desktop",
            "/home/abiorh/ai/",
        ],
    },
]
file_system_agent = OmniCoreAgent(
    name="FileSystemAgent",
    system_instruction=(
        """You are a File System Agent. Your task is to append the input string to 'system_status.md' in /home/abiorh/ai.
- If the file exists, append the content; if not, create it.
- After successfully writing the content, **do not call any more tools**.
- Immediately return a final agent response in the built-in output format.
- Do not add extra explanations, thoughts, or repeat the task."""
    ),
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    # embedding_config={"provider": "voyage", "model": "voyage-3.5", "dimensions": 1024, "encoding_format": "base64"},
    # local_tools=build_tool_registry_text_formatter_agent(),
    mcp_tools=FILE_SYSTEM_MCP_TOOLS,
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)


sequential_agent = SequentialAgent(
    sub_agents=[system_monitor_agent, text_formatter_agent, file_system_agent]
)


# async def main():
#     result = await sequential_agent()
#     print("Async SequentialAgent result:", result)

# if __name__ == "__main__":
#     asyncio.run(main())


async def run_sequential_agent(
    initial_task: str = None, session_id: str = None
) -> dict:
    try:
        # IMPORTANT: explicit initialize() call (developer-managed lifecycle)
        await sequential_agent.initialize()
        logger.info(f"Running Sequential Agent with initial task: {initial_task}")
        final_output = await sequential_agent.run(
            initial_task=initial_task, session_id=session_id
        )
        logger.info(f"Final output from Sequential Agent: {final_output}")
        return final_output
    finally:
        await sequential_agent.shutdown()


if __name__ == "__main__":
    test_task = "what is the system status of my computer"
    session_id = "test_session_001"

    result = asyncio.run(
        run_sequential_agent(initial_task=test_task, session_id=session_id)
    )
    print("Sequential Agent Result:", result)
    # test_task = "what is the system status of my computer"
    # session_id = "test_session_001"

    # result = asyncio.run(run_sequential_agent())
    # print("Sequential Agent Result:", result)
