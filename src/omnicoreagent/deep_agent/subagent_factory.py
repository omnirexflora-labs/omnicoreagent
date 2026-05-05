"""
SubagentFactory - Creates focused subagents for parallel work.

Subagents inherit:
- Parent's model config
- Parent's tools (MCP and local)
- Parent's agent_config (context_management, tool_offload, etc.)
- Focused task assignment via prompt_builder
- Memory path for writing findings
"""

import asyncio
import json
from typing import Any, Dict, List, Optional
from omnicoreagent.agent import OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.utils import logger


class SubagentFactory:
    """
    Factory for creating focused subagents.

    Subagents:
    - Inherit parent's model config, tools, AND agent_config
    - Get focused task via prompt
    - Write results to memory (not return through context)
    """

    def __init__(
        self,
        base_model_config: Dict[str, Any],
        mcp_tools: Optional[List[Dict]] = None,
        local_tools: Optional[ToolRegistry] = None,
        agent_config: Optional[Dict[str, Any]] = None,
        prompt_builder: Optional[Any] = None,
        event_router: Optional[Any] = None,
        memory_router: Optional[Any] = None,
        debug: Optional[bool] = False,
    ):
        """
        Initialize factory with shared configuration.

        Args:
            base_model_config: Model config all subagents use
            mcp_tools: MCP tools subagents can use
            local_tools: Local tools subagents can use
            agent_config: Full agent config (context_management, tool_offload, etc.)
            prompt_builder: DeepAgentPromptBuilder instance
            event_router: EventRouter instance
            memory_router: MemoryRouter instance
            debug: Debug mode
        """
        self.base_model_config = base_model_config
        self.mcp_tools = mcp_tools
        self.local_tools = local_tools
        self.event_router = event_router
        self.memory_router = memory_router
        self.debug = debug
        self.agent_config = agent_config or {}
        self.prompt_builder = prompt_builder
        self._active_subagents: Dict[str, OmniCoreAgent] = {}

    def _build_subagent_config(self) -> Dict[str, Any]:
        """
        Build agent_config for subagents inheriting parent's config.

        Subagents get full config but with some adjustments:
        - Fewer max_steps (focused task)
        - Use workspace-backed memory when memory tools are enabled
        """
        config = self.agent_config.copy()

        config["max_steps"] = min(config.get("max_steps", 15), 15)

        if "memory_tool_backend" not in config:
            config["memory_tool_backend"] = "workspace"

        return config

    def create_subagent(
        self,
        name: str,
        role: str,
        task: str,
        output_path: str,
    ) -> OmniCoreAgent:
        """
        Create a focused subagent.

        Args:
            name: Subagent identifier
            role: What this subagent specializes in
            task: Specific task to complete
            output_path: Memory path for writing findings

        Returns:
            Configured OmniCoreAgent ready to run
        """
        instruction = f"""
You are a specialized subagent with a focused task.

ROLE: {role}

TASK: {task}

OUTPUT REQUIREMENTS:
- Write your findings to: {output_path}
- Use memory_create_update tool to save your findings
- Be thorough but focused on YOUR specific task only
- Do NOT duplicate work of other subagents
- Structure your findings clearly with headers

When you have completed your investigation:
1. Save findings to the output_path using memory_create_update
2. Confirm you saved the findings
3. Return a brief summary of what you found
"""

        subagent_config = self._build_subagent_config()

        agent = OmniCoreAgent(
            name=f"subagent_{name}",
            system_instruction=instruction,
            model_config=self.base_model_config,
            agent_config=subagent_config,
            mcp_tools=self.mcp_tools,
            local_tools=self.local_tools,
            event_router=self.event_router,
            memory_router=self.memory_router,
            debug=self.debug,
        )

        self._active_subagents[name] = agent
        return agent

    async def run_subagent(
        self,
        name: str,
        role: str,
        task: str,
        output_path: str,
    ) -> Dict[str, Any]:
        """
        Create and run a subagent, return result.
        """
        logger.info(f"Spawning subagent '{name}' for task: {task[:50]}...")

        agent = self.create_subagent(
            name=name,
            role=role,
            task=task,
            output_path=output_path,
        )

        try:
            if self.mcp_tools:
                await agent.connect_mcp_servers()

            result = await agent.run(str(task))
            response = result.get("response", str(result)) or ""
            if not isinstance(response, str):
                response = str(response)

            # Check for error indicators in the response
            error_indicators = [
                "model encountered an error",
                "error occurred",
                "failed to",
                "unable to complete",
                "retry again",
            ]
            response_lower = response.lower()
            is_error = any(indicator in response_lower for indicator in error_indicators)
            
            # Also check if response is empty or too short
            is_error = is_error or len(response.strip()) < 10

            if is_error:
                logger.warning(f"Subagent '{name}' returned an error response")
                return {
                    "status": "error",
                    "data": {
                        "subagent_name": name,
                        "output_path": output_path,
                        "error": response[:500] if len(response) > 500 else response,
                    },
                    "message": f"Subagent '{name}' encountered an error: {response[:100]}",
                }

            logger.info(f"Subagent '{name}' completed task")

            return {
                "status": "success",
                "data": {
                    "subagent_name": name,
                    "output_path": output_path,
                    "summary": response[:500] if len(response) > 500 else response,
                },
                "message": f"Subagent '{name}' completed. Findings saved to {output_path}",
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Subagent '{name}' failed: {error_msg}")

            return {
                "status": "error",
                "data": {"subagent_name": name, "error": error_msg},
                "message": f"Subagent '{name}' failed: {error_msg}",
            }

        finally:
            await agent.cleanup()
            if name in self._active_subagents:
                del self._active_subagents[name]

    async def run_parallel_subagents(
        self,
        subagent_specs: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Run multiple subagents in parallel.
        """
        if not subagent_specs:
            return {
                "status": "success",
                "data": {"results": []},
                "message": "No subagents to spawn",
            }

        logger.info(f"Spawning {len(subagent_specs)} subagents in parallel")

        tasks = [
            self.run_subagent(
                name=spec.get("name", f"subagent_{i}"),
                role=spec.get("role", "Assistant"),
                task=spec.get("task", ""),
                output_path=spec.get(
                    "output_path", f"/memories/tasks/default/subagent_{i}/"
                ),
            )
            for i, spec in enumerate(subagent_specs)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        successful = 0
        failed = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(
                    {
                        "subagent_name": subagent_specs[i].get("name", f"subagent_{i}"),
                        "status": "error",
                        "error": str(result),
                    }
                )
                failed += 1
            else:
                processed_results.append(result.get("data", {}))
                if result.get("status") == "success":
                    successful += 1
                else:
                    failed += 1

        return {
            "status": "success"
            if failed == 0
            else "partial"
            if successful > 0
            else "error",
            "data": {
                "total": len(subagent_specs),
                "successful": successful,
                "failed": failed,
                "results": processed_results,
            },
            "message": f"Completed {successful}/{len(subagent_specs)} subagents successfully",
        }

    async def cleanup(self):
        """Clean up all active subagents."""
        for name, agent in list(self._active_subagents.items()):
            try:
                await agent.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up subagent '{name}': {e}")
        self._active_subagents.clear()


def build_subagent_tools(
    factory: SubagentFactory,
    registry: ToolRegistry,
) -> None:
    """
    Register subagent spawning tools with the given registry.
    """

    @registry.register_tool(
        name="spawn_subagent",
        description="""
    Spawns a specialized subagent to work on a focused task independently.
    
    Use this to delegate specific pieces of work to specialized workers.
    The subagent will investigate the task and write findings to the specified memory path.
    After completion, read the findings from memory using memory_view.
    
    When to use:
    - Task has a specific, focused scope
    - You need specialized expertise on a subtopic
    - You want to parallelize work (use spawn_parallel_subagents for multiple)
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": """
    Unique identifier for this subagent. Use lowercase with underscores.
    
    Examples:
    - "aws_analyst" for AWS research
    - "competitor_researcher" for competitor analysis
    - "pricing_expert" for pricing investigation
                    """,
                },
                "role": {
                    "type": "string",
                    "description": """
    What this subagent specializes in. Describes their expertise.
    
    Examples:
    - "AWS cloud infrastructure expert"
    - "Market research analyst specializing in fintech"
    - "Security vulnerability specialist"
                    """,
                },
                "task": {
                    "type": "string",
                    "description": """
    The specific task for the subagent to complete. Be clear and focused.
    
    Include:
    - What to investigate/produce
    - What format for findings
    - Any specific constraints or focus areas
    
    Example: "Research AWS AI/ML services including SageMaker, Bedrock. 
    Document pricing, key features, and ideal use cases. Compare to competitors."
                    """,
                },
                "output_path": {
                    "type": "string",
                    "description": """
    Memory path where subagent writes findings. Use descriptive paths.
    
    Format: /memories/{task_name}/subagent_{name}/findings.md
    
    Examples:
    - "/memories/cloud_comparison/subagent_aws/findings.md"
    - "/memories/market_research/subagent_competitors/findings.md"
                    """,
                },
            },
            "required": ["name", "role", "task", "output_path"],
            "additionalProperties": False,
        },
    )
    async def spawn_subagent(
        name: str,
        role: str,
        task: str,
        output_path: str,
    ) -> Dict[str, Any]:
        """
        Spawn a specialized subagent to work on a focused task.

        Parameters
        ----------
        name : str
            Unique identifier for this subagent
        role : str
            What this subagent specializes in
        task : str
            The specific task to complete
        output_path : str
            Memory path where subagent writes findings

        Returns
        -------
        dict
            {
                "status": "success" | "error",
                "data": {"subagent_name", "output_path", "summary"},
                "message": Completion message
            }
        """
        return await factory.run_subagent(
            name=name,
            role=role,
            task=task,
            output_path=output_path,
        )

    @registry.register_tool(
        name="spawn_parallel_subagents",
        description="""
    Spawns multiple subagents to work in parallel on independent tasks.
    
    Use this when you have multiple independent subtasks that can be investigated 
    simultaneously. Each subagent works on its own task and writes findings to memory.
    After completion, read all findings from memory to synthesize.
    
    When to use:
    - Task has multiple independent components
    - Research needed across different domains
    - Parallel exploration would be more efficient
    
    Example use case: Comparing 3 cloud providers
    - Spawn 3 subagents: aws_analyst, azure_analyst, gcp_analyst
    - Each researches their provider independently
    - Read all findings and synthesize comparison
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "subagents_json": {
                    "type": "string",
                    "description": """
    JSON array string of subagent specifications. Each spec needs:
    - name: Unique identifier (e.g., "aws_analyst")
    - role: Expertise description (e.g., "AWS cloud expert")
    - task: Specific task to complete
    - output_path: Memory path for findings
    
    Example:
    '[
        {"name": "aws", "role": "AWS expert", "task": "Research AWS ML services", "output_path": "/memories/cloud/aws.md"},
        {"name": "azure", "role": "Azure expert", "task": "Research Azure ML services", "output_path": "/memories/cloud/azure.md"}
    ]'
                    """,
                },
            },
            "required": ["subagents_json"],
            "additionalProperties": False,
        },
    )
    async def spawn_parallel_subagents(
        subagents_json: str,
    ) -> Dict[str, Any]:
        """
        Spawn multiple subagents to work in parallel.

        Parameters
        ----------
        subagents_json : str
            JSON array of subagent specs with name, role, task, output_path

        Returns
        -------
        dict
            {
                "status": "success" | "partial" | "error",
                "data": {"total", "successful", "failed", "results"},
                "message": Completion summary
            }
        """

        try:
            if isinstance(subagents_json, list):
                subagent_specs = subagents_json
            else:
                subagent_specs = json.loads(subagents_json)

            if not isinstance(subagent_specs, list):
                return {
                    "status": "error",
                    "data": None,
                    "message": "subagents_json must be a JSON array",
                }
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "data": None,
                "message": f"Invalid JSON: {str(e)}",
            }

        return await factory.run_parallel_subagents(subagent_specs)
