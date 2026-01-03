#!/usr/bin/env python3
"""
Sequential Workflow Example

Chain multiple agents where output of one becomes input for the next.
Example: Data Collector → Formatter → Reporter

Run:
    python cookbook/workflows/sequential_workflow.py
"""

import asyncio
from dotenv import load_dotenv

from omnicoreagent import (
    OmniCoreAgent,
    SequentialAgent,
    ToolRegistry,
    MemoryRouter,
    EventRouter,
)


def create_collector_tools() -> ToolRegistry:
    """Tools for the data collector agent."""
    registry = ToolRegistry()

    @registry.register_tool("get_system_info")
    def get_system_info() -> str:
        """Get current system information."""
        import platform
        import time
        return (
            f"OS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return registry


def create_formatter_tools() -> ToolRegistry:
    """Tools for the text formatter agent."""
    registry = ToolRegistry()

    @registry.register_tool("format_text")
    def format_text(text: str, style: str = "uppercase") -> str:
        """Format text in a specific style (uppercase, lowercase, title)."""
        if style == "uppercase":
            return text.upper()
        elif style == "lowercase":
            return text.lower()
        elif style == "title":
            return text.title()
        return text

    return registry


# Agent 1: Collects data
data_collector = OmniCoreAgent(
    name="DataCollector",
    system_instruction="Collect system information using the get_system_info tool.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=create_collector_tools(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Agent 2: Formats the data
text_formatter = OmniCoreAgent(
    name="TextFormatter",
    system_instruction="Format the input text to uppercase using the format_text tool.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    local_tools=create_formatter_tools(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Agent 3: Creates final report
reporter = OmniCoreAgent(
    name="Reporter",
    system_instruction="Summarize the input into a brief final report.",
    model_config={"provider": "openai", "model": "gpt-4o"},
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
)

# Create the sequential workflow
workflow = SequentialAgent(
    sub_agents=[data_collector, text_formatter, reporter]
)


async def main():
    load_dotenv()

    try:
        # Initialize all agents
        await workflow.initialize()
        print("Workflow initialized!")

        # Run the workflow
        print("\nRunning sequential workflow...")
        result = await workflow.run(
            initial_task="Get system information and create a formatted report",
            session_id="demo_session"
        )

        print(f"\nFinal Result:\n{result}")

    finally:
        # Clean up all agents
        await workflow.shutdown()
        print("\nWorkflow shut down.")


if __name__ == "__main__":
    asyncio.run(main())
