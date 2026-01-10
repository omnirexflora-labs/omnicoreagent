from omnicoreagent import (
    OmniCoreAgent,
    MemoryRouter,
    EventRouter,
    ToolRegistry,
    RouterAgent,
)
import asyncio

# this is for low level import
# from omnicoreagent.omni_agent.workflow.router_agent import RouterAgent


def build_tool_registry_google_search() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("google_search")
    def google_search(query: str) -> str:
        """Simulated Google Search tool"""
        return f"Search results for '{query}'"

    return registry


# --- General MCP tools---
GENERAL_MCP_TOOLS = [
    {
        "name": "tavily-remote-mcp",
        "transport_type": "streamable_http",
        "url": "https://mcp.tavily.com/mcp/?tavilyApiKey=<tavily api key>",
    }
]

# --- Researcher OmniCoreAgents---
renewable_energy_agent = OmniCoreAgent(
    name="RenewableEnergyResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in energy.
    Research the latest advancements in 'renewable energy sources'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    mcp_tools=GENERAL_MCP_TOOLS,
    local_tools=build_tool_registry_google_search(),
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)

ev_agent = OmniCoreAgent(
    name="EVResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in transportation.
    Research the latest developments in 'electric vehicle technology'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    mcp_tools=GENERAL_MCP_TOOLS,
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)

carbon_capture_agent = OmniCoreAgent(
    name="CarbonCaptureResearcher",
    system_instruction="""
    You are an AI Research Assistant specializing in climate solutions.
    Research the current state of 'carbon capture methods'.
    Use the Google Search tool provided.
    Summarize your key findings concisely (1-2 sentences).
    Output *only* the summary.
    """,
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 15, "tool_call_timeout": 60},
    mcp_tools=GENERAL_MCP_TOOLS,
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)


# --- RouterAgent (correctly receives model_config, agent_config, memory_router, event_router) ---
router_agent = RouterAgent(
    sub_agents=[renewable_energy_agent, ev_agent, carbon_capture_agent],
    model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
    agent_config={"max_steps": 8, "tool_call_timeout": 30},
    memory_router=MemoryRouter("in_memory"),
    event_router=EventRouter("in_memory"),
    debug=True,
)

# async def main():
#     # IMPORTANT: explicit initialize() call (developer-managed lifecycle)
#     await router_agent.initialize()

#     try:
#         query = "What are the latest trends in electric vehicle charging technology?"
#         session_id = str(uuid.uuid4())

#         logger.info(f"Running RouterAgent with session_id={session_id}, query={query}")
#         result = await router_agent.run(task=query, session_id=session_id)

#         print("RouterAgent result:")
#         print(result)

#     finally:
#         # Always cleanup in same loop
#         await router_agent.shutdown()


# if __name__ == "__main__":
#     asyncio.run(main())


async def main():
    result = await router_agent(
        task="What are the latest trends in EV charging technology?",
        session_id="test-session",
    )
    print("Async RouterAgent result:", result)


if __name__ == "__main__":
    asyncio.run(main())


# async def test_router():
#     try:
#         await router_agent.initialize()

#         tests = [
#             {
#                 "query": "What are the latest trends in electric vehicle charging technology?",
#                 "expected_agent": "EVResearcher",
#             },
#             {
#                 # Force a hallucination: mention a fake agent name
#                 "query": "Ask SolarResearcher about solar panel durability advances.",
#                 "expected_agent": "RenewableEnergyResearcher",  # should correct
#             },
#             {
#                 # Generic ambiguous query
#                 "query": "Tell me about climate solutions to reduce CO2.",
#                 "expected_agent": "CarbonCaptureResearcher",  # should map here
#             },
#             {
#                 # Completely irrelevant nonsense input
#                 "query": "Blorbity blop quantum unicorn.",
#                 "expected_agent": None,  # may fallback or return 'no match'
#             },
#         ]

#         for idx, test in enumerate(tests, start=1):
#             print(f"\n=== Test {idx}: {test['query']} ===")
#             result = await router_agent.run(
#                 task=test["query"], session_id=str(uuid.uuid4())
#             )
#             print("Result:", result)
#             if test["expected_agent"]:
#                 assert result["agent_name"] == test["expected_agent"], (
#                     f"Expected {test['expected_agent']} but got {result['agent_name']}"
#                 )
#             else:
#                 print("No expected agent — check guardrail behavior.")
#     finally:
#         # Always cleanup in same loop
#         await router_agent.shutdown()


# if __name__ == "__main__":
#     asyncio.run(test_router())
