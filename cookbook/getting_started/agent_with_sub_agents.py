#!/usr/bin/env python3
"""
Sub-Agents Example

OmniCoreAgent can manage sub-agents for complex, multi-step tasks.
The parent agent automatically delegates tasks to specialized sub-agents.

Features covered:
- Creating specialized sub-agents
- Passing sub-agents to parent agent
- Automatic task delegation

Build on: agent_with_metrics.py
This is the foundation for building multi-agent systems.

Run:
    python cookbook/getting_started/agent_with_sub_agents.py
"""

import asyncio

from omnicoreagent import OmniCoreAgent

from _bootstrap import model_config, require_llm_api_key, response_text


async def main():
    require_llm_api_key()

    print("=" * 60)
    print("SUB-AGENTS - Automatic Task Delegation")
    print("=" * 60)

    # Step 1: Create specialized sub-agents
    # Each sub-agent is a full OmniCoreAgent with its own expertise

    researcher = OmniCoreAgent(
        name="researcher",
        system_instruction="""You are a research specialist.
Your job is to gather and summarize information on topics.
Be concise - provide 2-3 key facts.""",
        model_config=model_config(max_tokens=600),
    )

    analyst = OmniCoreAgent(
        name="analyst",
        system_instruction="""You are an analysis specialist.
Your job is to analyze information and provide insights.
Be concise - provide 2-3 key insights.""",
        model_config=model_config(max_tokens=600),
    )

    writer = OmniCoreAgent(
        name="writer",
        system_instruction="""You are a writing specialist.
Your job is to take information and create polished, professional content.
Be concise - provide a brief, well-written summary.""",
        model_config=model_config(max_tokens=600),
    )

    # Step 2: Create parent agent with sub_agents parameter
    # The parent can now delegate tasks to these specialized agents

    coordinator = OmniCoreAgent(
        name="project_coordinator",
        system_instruction="""You are a project coordinator.
You have access to specialized sub-agents:
- researcher: For gathering information on topics
- analyst: For analyzing data and providing insights  
- writer: For creating polished content

Delegate tasks appropriately based on what the user needs.
Combine the outputs from sub-agents to provide complete answers.""",
        model_config=model_config(max_tokens=900),
        sub_agents=[researcher, analyst, writer],  # Pass sub-agents here
        agent_config={
            "max_steps": 15,  # Allow more steps for delegation
        },
        debug=True,
    )

    print("\n🤖 Agent Hierarchy Created:")
    print("  project_coordinator (parent)")
    print("    ├── researcher (sub-agent)")
    print("    ├── analyst (sub-agent)")
    print("    └── writer (sub-agent)")

    # Step 3: Run a task that requires coordination
    print("\n📋 Running task that requires delegation...")
    print("-" * 50)

    result = await coordinator.run(
        """I need help with quantum computing:
1. Research the key concepts
2. Analyze the current state of the field
3. Write a brief professional summary

Delegate to appropriate specialists."""
    )

    print(f"\n📝 Coordinator's Response:\n{response_text(result)}")

    # Cleanup all agents
    await researcher.cleanup()
    await analyst.cleanup()
    await writer.cleanup()
    await coordinator.cleanup()

    print("\n" + "=" * 60)
    print("HOW SUB-AGENTS WORK")
    print("=" * 60)
    print("""
1. CREATE SPECIALIZED AGENTS:
   researcher = OmniCoreAgent(name="researcher", ...)
   analyst = OmniCoreAgent(name="analyst", ...)

2. PASS TO PARENT AGENT:
   coordinator = OmniCoreAgent(
       name="coordinator",
       sub_agents=[researcher, analyst],  # <-- Pass list here
   )

3. PARENT DISCOVERS SUB-AGENTS:
   - The parent sees AVAILABLE SUB AGENT REGISTRY in its context
   - It can delegate tasks using agent_name and parameters
   - Sub-agent results are returned to the parent

WHEN TO USE SUB-AGENTS:
- Complex reasoning or analysis tasks
- Domain-specific expertise needed
- Multi-step workflows
- Parallel processing of multiple tasks

See cookbook/workflows/ for SequentialAgent, ParallelAgent, RouterAgent patterns!
""")


if __name__ == "__main__":
    asyncio.run(main())
