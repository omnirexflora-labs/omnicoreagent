from omnicoreagent.omni_agent.agent import OmniCoreAgent
from omnicoreagent.core.utils import logger
from typing import List, Optional
import asyncio
import uuid
import re


class RouterAgent:
    """
    Routes a task to the most suitable sub-agent using XML-based LLM decisions.
    - Accepts developer model_config, agent_config, memory_router, event_router.
    - Builds an internal RouterAgent OmniCoreAgent with those configs.
    - Eagerly connects MCP servers at startup.
    - Routes user task and executes chosen agent.
    """

    DEFAULT_TASK = "Please follow your system instructions and process accordingly."

    def __init__(
        self,
        sub_agents: List[OmniCoreAgent],
        model_config: dict,
        agent_config: dict,
        memory_router=None,
        event_router=None,
        debug: bool = False,
        max_retries: int = 3,
    ):
        if not sub_agents:
            raise ValueError("RouterAgent requires at least one sub-agent")

        self.sub_agents = {
            getattr(a, "name", f"Agent_{i + 1}"): a for i, a in enumerate(sub_agents)
        }
        self.max_retries = max_retries
        self.agent_registry: dict = {}
        self.model_config = model_config
        self.agent_config = agent_config
        self.memory_router = memory_router
        self.event_router = event_router
        self.debug = debug

        self._initialized = False

        self.router_agent = None

    async def initialize(self):
        """create the router agent instance, generate each agent capabilities, and connect to MCP servers for and all sub-agents."""
        if self._initialized:
            return
        logger.info("RouterAgent: Initializing MCP servers for router and sub-agents")
        for agent in list(self.sub_agents.values()):
            if getattr(agent, "mcp_tools", None):
                try:
                    await agent.connect_mcp_servers()
                    logger.info(f"{agent.name}: MCP servers connected")
                except Exception as exc:
                    logger.warning(f"{agent.name}: MCP connection failed: {exc}")
            await self.create_agent_capabilities_registry(agent=agent)

        if not self.router_agent:
            system_instruction = self._build_router_system_instruction()
            self.router_agent = OmniCoreAgent(
                name="RouterAgent",
                system_instruction=system_instruction,
                model_config=self.model_config,
                agent_config=self.agent_config,
                memory_router=self.memory_router,
                event_router=self.event_router,
                debug=self.debug,
            )

        self._initialized = True

    async def create_agent_capabilities_registry(self, agent) -> str:
        """
        Generate a system prompt instructing an LLM to summarize the agent's capabilities.

        The LLM should produce 2-5 concise sentences that describe everything the agent can do
        based on its system instruction and tools.

        Parameters:
            agent: The agent instance. Must have `system_instruction` and optional `tools` attributes.

        Returns:
            A string system prompt for the LLM.
        """
        agent_available_tools = await agent.list_all_available_tools()
        agent_system_intruction = getattr(
            agent, "system_instruction", "No description provided."
        )
        agent_name = getattr(agent, "name", "")

        tools_text = ""
        if agent_available_tools:
            tools_text = " The agent has the following tools available:\n" + "\n".join(
                [
                    f"- {tool['name']}: {tool['description']}"
                    for tool in agent_available_tools
                ]
            )

        system_prompt = f"""
    You are an expert summarizer. Your task is to read the following agent description and its tools, 
    and generate 2-5 concise sentences that clearly describe all the capabilities of this agent. 
    The summary should allow any LLM to understand everything this agent can do.

    Agent description:
    {agent_system_intruction}

    Agent tools:
    {tools_text}

    Instructions for your response:
    - Produce 2-5 sentences only.
    - Capture all capabilities and uses of the agent and its tools.
    - Write in clear, natural language.
    - Do not include any formatting or XML, just plain text.
    """

        try:
            response = await agent.llm_connection.llm_call(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": "Please process the request system prompt correctly",
                    },
                ]
            )
            if response:
                if hasattr(response, "choices"):
                    response = response.choices[0].message.content.strip()
            elif hasattr(response, "message"):
                response = response.message.content.strip()

            self.agent_registry[agent_name] = response
        except Exception as e:
            logger.info(f"error occurs during agent registry process: {str(e)}")
            self.agent_registry[agent_name] = agent_system_intruction

    async def run(
        self, task: Optional[str] = None, session_id: Optional[str] = None
    ) -> dict:
        """Route the task to the correct agent and return its response with guardrails."""
        if not task:
            task = self.DEFAULT_TASK
        if not session_id:
            session_id = str(uuid.uuid4())

        if not self._initialized:
            raise RuntimeError(
                "This RouterAgent instance has not been initialized. "
                "Call `await <your_instance>.initialize()` before using it."
            )

        logger.info(f"RouterAgent: Routing task -> {task}")

        retry_count = 0
        chosen_agent = None
        query = task

        while retry_count < self.max_retries:
            parsed_response = await self._route_with_llm(query, session_id)

            match_agent = re.search(r"<agent>(.*?)</agent>", parsed_response, re.DOTALL)
            match_task = re.search(r"<task>(.*?)</task>", parsed_response, re.DOTALL)

            if match_agent:
                agent_name = match_agent.group(1).strip()
                query = match_task.group(1).strip() if match_task else task

                if agent_name in self.sub_agents:
                    chosen_agent = self.sub_agents[agent_name]
                    break

            retry_count += 1
            logger.warning(
                f"RouterAgent: Invalid routing decision, retry {retry_count}/{self.max_retries}"
            )

            available_agents = "\n".join(
                f"<agent>{name}</agent>: {capabilities}"
                for name, capabilities in self.agent_registry.items()
            )
            query = (
                f"The last decision was invalid. "
                f"You MUST pick one of the following agents:\n{available_agents}\n\n"
                f"User task: {task}"
            )

        if not chosen_agent:
            return {
                "error": f"RouterAgent could not resolve a valid agent after {self.max_retries} retries.",
                "session_id": session_id,
                "response": task,
            }

        return await self._run_single_agent(chosen_agent, query, session_id)

    async def _run_single_agent(
        self, agent: OmniCoreAgent, query: str, session_id: str
    ) -> dict:
        """Executes the selected agent with retries, production ready."""
        agent_name = getattr(agent, "name", "UnknownAgent")
        final_output = {}
        retry_count = 0

        while retry_count < self.max_retries:
            try:
                final_output = await asyncio.shield(
                    agent.run(query=query, session_id=session_id)
                )

                final_output.setdefault("agent_name", agent_name)
                final_output.setdefault("session_id", session_id)
                final_output.setdefault("response", "")

                break

            except Exception as exc:
                retry_count += 1
                logger.warning(
                    f"{agent_name}: Attempt {retry_count}/{self.max_retries} failed: {exc}"
                )

                if retry_count >= self.max_retries:
                    final_output = {
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "response": query,
                        "error": str(exc),
                    }
                    logger.error(f"{agent_name}: Max retries reached with error {exc}")
                    break

        return final_output

    async def _route_with_llm(self, task: str, session_id: str) -> str:
        """Use RouterCAgent OmniCoreAgent to pick the agent via XML decision."""
        response = await self.router_agent.run(query=task, session_id=session_id)
        return response.get("response", "")

    def _build_router_system_instruction(self) -> str:
        """Construct XML-based system instruction for routing decisions with agent capabilities."""
        agent_list = "\n".join(
            f"<agent>\n"
            f"  <name>{name}</name>\n"
            f"  <capabilities>{capabilities}</capabilities>\n"
            f"</agent>"
            for name, capabilities in self.agent_registry.items()
        )

        return f"""
    You are a Router Agent. Your role is to analyze the user query and decide which sub-agent should handle it.

    Available agents (name + capabilities):
    {agent_list}

    Rules:
    - ALWAYS output valid XML.
    - You MUST use <thought> and <final_answer> tags for all responses.
    - Inside <thought>, explain your reasoning for choosing the agent.
    - Inside <final_answer>, wrap your decision in <routing>...</routing>.
        - <routing> must contain exactly one <agent> (the name only).
        - <task> must be the original user query verbatim.
    - Do not include anything outside <thought> and <final_answer>.
    - Choose the BEST MATCH based on the agent descriptions.

    Example of your Final Answer:
    <thought>
    The user wants to know about Python coding. Among the available agents, the CodeWriter specializes in implementing code. This makes it the best fit.
    </thought>
    <final_answer>
        <routing>
            <agent>CodeWriter</agent>
            <task>Implement a Python function to reverse a string</task>
        </routing>
    </final_answer>
    """

    async def __call__(
        self, task: Optional[str] = None, session_id: Optional[str] = None
    ):
        auto_init = not self._initialized
        try:
            if auto_init:
                await self.initialize()
            return await self.run(task=task, session_id=session_id)
        finally:
            if auto_init:
                await self.shutdown()

    async def shutdown(self):
        for agent in list(self.sub_agents.values()) + [self.router_agent]:
            if getattr(agent, "mcp_tools", None):
                try:
                    await agent.cleanup()
                    logger.info(f"{agent.name}: MCP cleanup successful")
                except Exception as exc:
                    logger.warning(f"{agent.name}: MCP cleanup failed: {exc}")
