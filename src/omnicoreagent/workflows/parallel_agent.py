from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from typing import List, Optional, Dict
from omnicoreagent.core.utils import logger
import asyncio
import uuid


class ParallelAgent:
    """Runs a list of OmniCoreAgents in parallel, each with its own optional task, sharing a session ID if provided."""

    DEFAULT_TASK = "Please follow your system instructions and process accordingly."

    def __init__(self, sub_agents: List[OmniCoreAgent], max_retries: int = 3):
        if not sub_agents:
            raise ValueError("ParallelAgent requires at least one sub-agent")
        self.sub_agents = sub_agents
        self.max_retries = max_retries
        self._initialized = False

    async def initialize(self):
        """Connect MCP servers for RouterCore and all sub-agents."""
        if self._initialized:
            return
        logger.info("RouterAgent: Initializing MCP servers for router and sub-agents")
        for agent in self.sub_agents:
            if getattr(agent, "mcp_tools", None):
                try:
                    await agent.connect_mcp_servers()
                    logger.info(f"{agent.name}: MCP servers connected")
                except Exception as exc:
                    logger.warning(f"{agent.name}: MCP connection failed: {exc}")
        self._initialized = True

    async def run(
        self,
        agent_tasks: Optional[Dict[str, Optional[str]]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not self._initialized:
            raise RuntimeError(
                "ParalleAgent must be initialized `Call `await <your_instance>.initialize()` before using it`"
            )
        if not session_id:
            session_id = str(uuid.uuid4())

        tasks = []
        for idx, agent_service in enumerate(self.sub_agents, start=1):
            agent_name = getattr(agent_service, "name", f"Agent_{idx}")
            task = (agent_tasks or {}).get(agent_name) or self.DEFAULT_TASK
            query = task
            tasks.append(
                asyncio.create_task(
                    self._run_single_agent(agent_service, query, session_id, idx)
                )
            )

        results = await asyncio.gather(*tasks)
        return {res["agent_name"]: res for res in results}

    async def _run_single_agent(
        self, agent_service: OmniCoreAgent, query: str, session_id: str, idx: int
    ) -> dict:
        """Runs an agent with retry logic and MCP management."""
        agent_name = getattr(agent_service, "name", f"Agent_{idx}")
        final_output = {}
        retry_count = 0

        while retry_count < self.max_retries:
            try:
                final_output = await asyncio.shield(
                    agent_service.run(query=query, session_id=session_id)
                )
                break

            except Exception as exc:
                retry_count += 1
                logger.warning(
                    f"{agent_name}: Attempt {retry_count}/{self.max_retries} failed: {exc}"
                )
                if retry_count >= self.max_retries:
                    logger.error(f"{agent_name}: Max retries reached")
                    final_output = {
                        "response": query,
                        "session_id": session_id,
                        "failed_agent": agent_name,
                        "error": str(exc),
                    }
                    break

        return {**final_output}

    async def __call__(
        self,
        agent_tasks: Optional[Dict[str, Optional[str]]] = None,
        session_id: Optional[str] = None,
    ):
        auto_init = not self._initialized
        try:
            if auto_init:
                await self.initialize()
            return await self.run(agent_tasks=agent_tasks, session_id=session_id)
        finally:
            if auto_init:
                await self.shutdown()

    async def shutdown(self):
        for agent in self.sub_agents:
            if getattr(agent, "mcp_tools", None):
                try:
                    await agent.cleanup()
                    logger.info(f"{agent.name}: MCP cleanup successful")
                except Exception as exc:
                    logger.warning(f"{agent.name}: MCP cleanup failed: {exc}")
