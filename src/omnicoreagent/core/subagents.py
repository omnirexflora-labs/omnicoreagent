"""
SubagentFactory - Creates focused subagents for parallel work.

Subagents inherit:
- Parent's model config
- Parent's tools (MCP and local)
- Parent's agent_config (context_management, tool_offload, etc.)
- Focused task assignment via prompt_builder
- Workspace file path for writing output
"""

import asyncio
import json
from typing import Any, Dict, List, Optional
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.logging import logger


class SubagentFactory:
    """
    Factory for creating focused subagents.

    Subagents:
    - Inherit parent's model config, tools, AND agent_config
    - Get focused task via prompt
    - Write output to workspace files instead of returning large payloads
    """

    def __init__(
        self,
        base_model_config: Dict[str, Any],
        mcp_tools: Optional[List[Dict]] = None,
        local_tools: Optional[ToolRegistry] = None,
        agent_config: Optional[Dict[str, Any]] = None,
        prompt_builder: Optional[Any] = None,
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
            prompt_builder: Optional prompt builder with build_subagent_prompt support
            memory_router: MemoryRouter instance
            debug: Debug mode
        """
        self.base_model_config = base_model_config
        self.mcp_tools = mcp_tools
        self.local_tools = local_tools
        self.memory_router = memory_router
        self.debug = debug
        self.agent_config = agent_config or {}
        self.prompt_builder = prompt_builder
        self._active_subagents: Dict[str, Any] = {}

    def _build_subagent_config(self) -> Dict[str, Any]:
        """
        Build agent_config for subagents inheriting parent's config.

        Subagents get full config but with some adjustments:
        - Fewer max_steps (focused task)
        - Workspace files are always enabled for writing output
        - Dynamic delegation stays on the lead agent only
        """
        config = self.agent_config.copy()

        config["max_steps"] = min(config.get("max_steps", 15), 15)
        config["enable_subagents"] = False
        config["enable_workspace_files"] = True

        return config

    def _build_subagent_instruction(
        self,
        *,
        role: str,
        task: str,
        output_path: str,
    ) -> str:
        """Build the focused system instruction for a spawned worker."""
        if self.prompt_builder and hasattr(
            self.prompt_builder, "build_subagent_prompt"
        ):
            return self.prompt_builder.build_subagent_prompt(
                role=role,
                task=task,
                output_path=output_path,
            )

        return f"""
You are a specialized subagent assigned to execute one focused task.

ROLE: {role}

TASK: {task}

OUTPUT REQUIREMENTS:
- Write your output to: {output_path}
- Use write_file tool to save your output
- Be thorough but focused on YOUR specific task only
- Do not duplicate work assigned to other subagents
- Structure your output clearly with headers

When you have completed the task:
1. Save output to the output_path using write_file
2. Confirm you saved the output
3. Return a brief summary of the completed output
"""

    def _build_subagent_local_tools(self) -> Optional[ToolRegistry]:
        """
        Give subagents inherited tools without the parent delegation tool.

        The lead agent owns dynamic delegation. Spawned workers stay focused and
        should not recursively spawn more workers through the shared registry.
        """
        if self.local_tools is None:
            return None
        if not isinstance(self.local_tools, ToolRegistry):
            return self.local_tools

        registry = ToolRegistry()
        for tool in self.local_tools.list_tools():
            if tool.name == "spawn_subagents":
                continue
            registry.register(tool)
        return registry

    def create_subagent(
        self,
        name: str,
        role: str,
        task: str,
        output_path: str,
    ):
        """
        Create a focused subagent.

        Args:
            name: Subagent identifier
            role: What this subagent specializes in
            task: Specific task to complete
            output_path: Workspace file path for writing output

        Returns:
            Configured OmniCoreAgent ready to run
        """
        instruction = self._build_subagent_instruction(
            role=role,
            task=task,
            output_path=output_path,
        )

        subagent_config = self._build_subagent_config()

        from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

        agent = OmniCoreAgent(
            name=f"subagent_{name}",
            system_instruction=instruction,
            model_config=self.base_model_config,
            agent_config=subagent_config,
            mcp_tools=self.mcp_tools,
            local_tools=self._build_subagent_local_tools(),
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
            is_error = any(
                indicator in response_lower for indicator in error_indicators
            )

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
                "message": f"Subagent '{name}' completed. Output saved to {output_path}",
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
                    "output_path", f"/workspace/tasks/default/subagent_{i}/"
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
        name="spawn_subagents",
        description="""
    Spawns one or more subagents to work on focused tasks.

    Always pass a JSON array of subagent specs. If you only need one subagent,
    pass an array with one item. Multiple specs run in parallel.
    Each subagent writes output to workspace files. After completion, read
    all output paths with read_file before synthesizing.

    When to use:
    - Task has multiple independent components
    - Work is split across different domains, files, systems, or specialties
    - Parallel execution would be more efficient
    - One focused worker is enough, but keeping one array-based tool avoids
      choosing between single and parallel spawn modes

    Example use case: Coordinating a product audit
    - Spawn subagents for API review, UI review, docs review, and test review
    - Each worker executes its assigned task independently
    - Read all outputs and synthesize the final result
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "subagents_json": {
                    "type": "string",
                    "description": """
    JSON array string of subagent specifications. Each spec needs:
    - name: Unique identifier (e.g., "aws_analyst")
    - role: Worker role or expertise description (e.g., "API reviewer")
    - task: Specific task to complete
    - output_path: Workspace file path for output

    Example:
    '[
        {"name": "api", "role": "API reviewer", "task": "Review API error handling and write concrete risks", "output_path": "/workspace/audit/api.md"},
        {"name": "tests", "role": "Test reviewer", "task": "Review test coverage gaps and write recommended cases", "output_path": "/workspace/audit/tests.md"}
    ]'
                    """,
                },
            },
            "required": ["subagents_json"],
            "additionalProperties": False,
        },
    )
    async def spawn_subagents(
        subagents_json: str,
    ) -> Dict[str, Any]:
        """
        Spawn one or more subagents.

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
