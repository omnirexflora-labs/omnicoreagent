"""Run an OmniCoreAgent task through the durable background manager."""

import asyncio
import os

from omnicoreagent import BackgroundAgentManager, OmniCoreAgent


async def main():
    if not os.getenv("LLM_API_KEY"):
        print("Set LLM_API_KEY to run this live background agent example.")
        return

    agent = OmniCoreAgent(
        name="background_researcher",
        system_instruction="You write short, durable background task reports.",
        model_config={"provider": "openai", "model": "gpt-4o-mini"},
    )

    manager = BackgroundAgentManager(task_store="in_memory")
    await manager.register_agent(agent_id="background_researcher", agent=agent)
    await manager.register_task(
        task_id="daily_research_note",
        agent_id="background_researcher",
        query="Write a short research note about one practical AI agent reliability risk.",
        schedule={"type": "manual"},
        timeout_seconds=60,
        retry_policy={"max_retries": 1, "initial_delay_seconds": 0},
    )

    run = await manager.run_now("daily_research_note", wait=True)
    print(f"run_id={run.run_id}")
    print(f"status={run.status.value}")
    print(f"workspace={run.workspace_path}")
    print(run.result_preview)


if __name__ == "__main__":
    asyncio.run(main())
