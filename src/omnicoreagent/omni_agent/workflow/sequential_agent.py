from omnicoreagent.omni_agent.agent import OmniCoreAgent
from typing import List, Optional
from omnicoreagent.core.utils import logger
import uuid


class SequentialAgent:
    """Runs a list of OmniCoreAgents sequentially, passing output from one to the next with retry support."""

    DEFAULT_TASK = "Please follow your system instructions and process accordingly."

    def __init__(self, sub_agents: List[OmniCoreAgent], max_retries: int = 3):
        if not sub_agents:
            raise ValueError("SequentialAgent requires at least one sub-agent")
        self.sub_agents = sub_agents
        self.max_retries = max_retries
        self._initialized = False

    async def initialize(self):
        """Connect MCP servers for all sub-agents."""
        if self._initialized:
            return
        logger.info("SequentialAgent: Initializing MCP servers for sub-agents")
        for agent in self.sub_agents:
            if getattr(agent, "mcp_tools", None):
                try:
                    await agent.connect_mcp_servers()
                    logger.info(f"{agent.name}: MCP servers connected")
                except Exception as exc:
                    logger.warning(f"{agent.name}: MCP connection failed: {exc}")
        self._initialized = True

    async def run(self, initial_task: str = None, session_id: str = None) -> dict:
        if not self._initialized:
            raise RuntimeError(
                "SequentialAgent must be initialized Call `await <your_instance>.initialize()` before using it"
            )

        if not initial_task:
            initial_task = self.DEFAULT_TASK
        current_input = initial_task
        final_output: dict = {}

        if not session_id:
            session_id = str(uuid.uuid4())

        for idx, agent_service in enumerate(self.sub_agents, start=1):
            agent_name = getattr(agent_service, "name", f"Agent_{idx}")
            logger.info(f"Running agent {idx}/{len(self.sub_agents)}: {agent_name}")

            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    final_output = await agent_service.run(
                        query=current_input, session_id=session_id
                    )

                    break

                except Exception as exc:
                    retry_count += 1
                    logger.warning(
                        f"{agent_name}: Attempt {retry_count}/{self.max_retries} failed: {exc}"
                    )
                    if retry_count >= self.max_retries:
                        logger.error(
                            f"{agent_name}: Max retries reached, stopping SequentialAgent"
                        )
                        return {
                            "response": current_input,
                            "session_id": session_id,
                            "failed_agent": agent_name,
                            "error": str(exc),
                        }

            current_input = self._extract_output(final_output)

        return final_output

    @staticmethod
    def _extract_output(agent_output: dict) -> str:
        """Safely extract the response text from an agent's output dict."""
        return agent_output.get("response", "")

    async def __call__(
        self, initial_task: Optional[str] = None, session_id: Optional[str] = None
    ):
        auto_init = not self._initialized
        try:
            if auto_init:
                await self.initialize()
            return await self.run(initial_task=initial_task, session_id=session_id)
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
