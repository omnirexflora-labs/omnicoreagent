"""Run an OmniCoreAgent task through the background manager."""

import asyncio

try:
    from cookbook.background_agents._bootstrap import ROOT_DIR  # noqa: F401
except ModuleNotFoundError:
    from _bootstrap import ROOT_DIR  # noqa: F401
from cookbook.shared import model_config, require_llm_api_key
from omnicoreagent import BackgroundAgentManager, OmniCoreAgent


async def main():
    require_llm_api_key()

    agent = OmniCoreAgent(
        name="background_researcher",
        system_instruction="You write short background task reports.",
        model_config=model_config(),
    )

    manager = BackgroundAgentManager()
    try:
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
        attempts = await manager.list_attempts(run.run_id)
        events = await manager.get_run_events(run.run_id)

        print(f"run_id={run.run_id}")
        print(f"status={run.status.value}")
        print(f"workspace={run.workspace_path}")
        print(f"attempts={len(attempts)}")
        print(f"events={[event['event'] for event in events]}")
        print(run.result_preview)
    finally:
        await manager.shutdown()
        if hasattr(agent, "cleanup"):
            await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
