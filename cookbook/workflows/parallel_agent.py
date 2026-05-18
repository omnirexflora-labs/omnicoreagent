from omnicoreagent import (
    OmniCoreAgent,
    MemoryRouter,
    ToolRegistry,
    ParallelAgent,
    logger,
)
from typing import Optional, Dict

import asyncio
import uuid

from _bootstrap import model_config, require_llm_api_key


# Example tool: Google Search
def build_tool_registry_google_search() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("google_search")
    def google_search(query: str) -> str:
        """Simulated Google Search tool"""
        return f"Search results for '{query}'"

    return registry


# --- Researcher Agents ---
google_search_tool = build_tool_registry_google_search()

# Researcher 1: Renewable Energy
renewable_energy_agent = OmniCoreAgent(
    name="RenewableEnergyResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in energy.
    Research the latest advancements in 'renewable energy sources'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config=model_config(temperature=0.3, max_tokens=600),
    agent_config={"max_steps": 8, "tool_call_timeout": 30},
    local_tools=google_search_tool,
    memory_router=MemoryRouter("in_memory"),
    debug=True,
)

# Researcher 2: Electric Vehicles
ev_agent = OmniCoreAgent(
    name="EVResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in transportation.
    Research the latest developments in 'electric vehicle technology'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config=model_config(temperature=0.3, max_tokens=600),
    agent_config={"max_steps": 8, "tool_call_timeout": 30},
    local_tools=google_search_tool,
    memory_router=MemoryRouter("in_memory"),
    debug=True,
)

# Researcher 3: Carbon Capture
carbon_capture_agent = OmniCoreAgent(
    name="CarbonCaptureResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in climate solutions.
    Research the current state of 'carbon capture methods'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config=model_config(temperature=0.3, max_tokens=600),
    agent_config={"max_steps": 8, "tool_call_timeout": 30},
    local_tools=google_search_tool,
    memory_router=MemoryRouter("in_memory"),
    debug=True,
)

# --- Parallel Researcher Agent Workflow ---
researcher_parallel_agent = ParallelAgent(
    sub_agents=[renewable_energy_agent, ev_agent, carbon_capture_agent]
)

async def run_parallel_researchers(
    agent_tasks: Optional[Dict[str, Optional[str]]] = None,
    session_id: Optional[str] = None,
) -> dict:
    """
    Run all researcher agents in parallel.

    agent_tasks: Optional dict {agent_name: task_string | None}. If None, default task is used.
    session_id: Shared session ID (optional, auto-generated if None)
    """
    try:
        # IMPORTANT: explicit initialize() call (developer-managed lifecycle)
        await researcher_parallel_agent.initialize()
        if not session_id:
            session_id = str(uuid.uuid4())

        logger.info(f"Running Parallel Researchers with session_id: {session_id}")
        results = await researcher_parallel_agent.run(
            agent_tasks=agent_tasks, session_id=session_id
        )
        return results
    finally:
        # Always cleanup in same loop
        await researcher_parallel_agent.shutdown()


async def main():
    require_llm_api_key()
    tasks = {
        "RenewableEnergyResearcher": "Summarize recent renewable energy innovations",
        "EVResearcher": None,  # Will use default internal task
        "CarbonCaptureResearcher": "Provide key findings on carbon capture technologies",
    }

    result = await run_parallel_researchers(agent_tasks=tasks)
    print("Parallel Researcher Results:", result)


if __name__ == "__main__":
    asyncio.run(main())
