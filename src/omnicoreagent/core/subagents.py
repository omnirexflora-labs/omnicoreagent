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
from typing import Any, Dict, List, Optional
from omnicoreagent.core.tools.local_tools_registry import INTERNAL_TOOL_PROVIDERS, ToolRegistry
from omnicoreagent.core.logging import logger
from omnicoreagent.core.budgets import current_budgets
from omnicoreagent.governance.capabilities import subagent_spawn_authority_requests
from omnicoreagent.governance.snapshots import derive_subagent_policy
from omnicoreagent.core.workspace.paths import WORKSPACE_FILE_PATH_PREFIXES
from omnicoreagent.core.agents.subagent_helpers import (
    accepts_run_id,
    find_child_trace_id,
    finish_delegation,
    new_child_run_id,
)
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor


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
        governance_engine: Optional[Any] = None,
        telemetry_recorder: Optional[Any] = None,
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
            governance_engine: Optional governed execution policy engine
            telemetry_recorder: Parent recorder used for child trace correlation
            debug: Debug mode
        """
        self.base_model_config = base_model_config
        self.mcp_tools = mcp_tools
        self.local_tools = local_tools
        self.memory_router = memory_router
        self.debug = debug
        self.agent_config = agent_config or {}
        self.prompt_builder = prompt_builder
        self.governance_engine = governance_engine
        self.telemetry_recorder = telemetry_recorder
        self._active_subagents: Dict[str, Any] = {}

    def _build_subagent_config(
        self, *, subagent_name: str = "subagent"
    ) -> Dict[str, Any]:
        """
        Build agent_config for subagents inheriting parent's config.

        Subagents get full config but with some adjustments:
        - A bounded 50-step budget for focused tasks
        - Workspace files are always enabled for writing output
        - Dynamic delegation stays on the lead agent only
        """
        config = self.agent_config.copy()

        config["max_steps"] = min(config.get("max_steps", 50), 50)
        config["enable_subagents"] = False
        config["enable_workspace_files"] = True
        context_management = dict(config.get("context_management") or {})
        context_management["enabled"] = True
        config["context_management"] = context_management
        tool_offload = dict(config.get("tool_offload") or {})
        tool_offload["enabled"] = True
        config["tool_offload"] = tool_offload
        if self.governance_engine is not None:
            governance_config = dict(config.get("governance_config") or {})
            governance_config.update(
                {
                    "enabled": True,
                    "policy": derive_subagent_policy(
                        self.governance_engine.policy,
                        subagent_name=subagent_name,
                    ),
                }
            )
            config["governance_config"] = governance_config

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
            # Keep every built-in provider label: governance decides a tool by
            # it (a worker's `execute` must stay `sandbox.execute`, not a
            # plain local tool call).
            provider = self.local_tools.get_tool_provider(tool.name)
            if provider in INTERNAL_TOOL_PROVIDERS:
                registry.mark_internal_tool_provider(tool.name, provider)
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

        subagent_config = self._build_subagent_config(subagent_name=name)

        from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

        agent = OmniCoreAgent(
            name=f"subagent_{name}",
            system_instruction=instruction,
            model_config=self.base_model_config,
            agent_config=subagent_config,
            mcp_tools=self.mcp_tools,
            local_tools=self._build_subagent_local_tools(),
            memory_router=self.memory_router,
            telemetry_store=(
                self.telemetry_recorder.store
                if self.telemetry_recorder is not None
                else None
            ),
            telemetry_recorder=self.telemetry_recorder,
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

        The spawn is recorded as a ``subagent.run`` delegation span on the
        parent trace. The child's run id is assigned before it starts, so the
        delegation, the child trace, and the returned result stay linked on
        success, error, and cancellation; the workspace output check is part
        of the delegation evidence.
        """
        logger.info(f"Spawning subagent '{name}' for task: {task[:50]}...")

        agent = self.create_subagent(
            name=name,
            role=role,
            task=task,
            output_path=output_path,
        )
        child_run_id = new_child_run_id() if accepts_run_id(agent) else None
        delegation = await self._start_delegation(
            agent=agent,
            name=name,
            role=role,
            task=task,
            output_path=output_path,
            child_run_id=child_run_id,
        )
        child_trace_id = None

        try:
            if self.mcp_tools:
                await agent.connect_mcp_servers()

            result = await agent.run(
                str(task), **({"run_id": child_run_id} if child_run_id else {})
            )
            child_trace_id = result.get("trace_id")
            child_run_id = result.get("run_id") or child_run_id
            response = result.get("response", str(result)) or ""
            if not isinstance(response, str):
                response = str(response)

            is_error = result.get("status", "success") != "success"

            if is_error:
                logger.warning(f"Subagent '{name}' returned an error response")
                await self._finish_delegation(
                    delegation,
                    child_run_id=child_run_id,
                    child_trace_id=child_trace_id,
                    status=SpanStatus.ERROR,
                    error={"type": "SubagentError", "message": response[:500]},
                )
                return {
                    "status": "error",
                    "data": {
                        "subagent_name": name,
                        "output_path": output_path,
                        "trace_id": child_trace_id,
                        "run_id": child_run_id,
                        "error": response[:500] if len(response) > 500 else response,
                        "governance": self._governance_reference(),
                    },
                    "message": f"Subagent '{name}' encountered an error: {response[:100]}",
                }

            output_error = self._workspace_output_error(agent, output_path)
            workspace_output = {
                "path": output_path,
                "verified": output_error is None,
                "error": output_error,
            }
            if output_error is not None:
                logger.warning(
                    "Subagent '%s' completed without a usable workspace output: %s",
                    name,
                    output_error,
                )
                await self._finish_delegation(
                    delegation,
                    child_run_id=child_run_id,
                    child_trace_id=child_trace_id,
                    status=SpanStatus.ERROR,
                    error={"type": "MissingWorkspaceOutput", "message": output_error},
                    workspace_output=workspace_output,
                )
                return {
                    "status": "error",
                    "data": {
                        "subagent_name": name,
                        "output_path": output_path,
                        "trace_id": child_trace_id,
                        "run_id": child_run_id,
                        "error": output_error,
                        "summary": response[:500] if len(response) > 500 else response,
                        "termination_reason": "missing_output",
                        "governance": self._governance_reference(),
                    },
                    "message": f"Subagent '{name}' did not create the requested output: {output_path}",
                }

            logger.info(f"Subagent '{name}' completed task")
            await self._finish_delegation(
                delegation,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
                status=SpanStatus.OK,
                workspace_output=workspace_output,
            )

            return {
                "status": "success",
                "data": {
                    "subagent_name": name,
                    "output_path": output_path,
                    "trace_id": child_trace_id,
                    "run_id": child_run_id,
                    "summary": response[:500] if len(response) > 500 else response,
                    "governance": self._governance_reference(),
                },
                "message": f"Subagent '{name}' completed. Requested output path: {output_path}",
            }

        except asyncio.CancelledError as e:
            await self._finish_delegation(
                delegation,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
                status=SpanStatus.CANCELLED,
                error={"type": e.__class__.__name__, "message": "cancelled"},
            )
            raise

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Subagent '{name}' failed: {error_msg}")
            child_trace_id = child_trace_id or await find_child_trace_id(
                self.telemetry_recorder,
                run_id=child_run_id,
                parent_trace_id=delegation["parent_trace_id"],
            )
            await self._finish_delegation(
                delegation,
                child_run_id=child_run_id,
                child_trace_id=child_trace_id,
                status=SpanStatus.ERROR,
                error={"type": e.__class__.__name__, "message": error_msg},
            )

            return {
                "status": "error",
                "data": {
                    "subagent_name": name,
                    "output_path": output_path,
                    "trace_id": child_trace_id,
                    "run_id": child_run_id,
                    "error": error_msg,
                    "governance": self._governance_reference(),
                },
                "message": f"Subagent '{name}' failed: {error_msg}",
            }

        finally:
            await agent.cleanup()
            if name in self._active_subagents:
                del self._active_subagents[name]

    async def _start_delegation(
        self,
        *,
        agent: Any,
        name: str,
        role: str,
        task: str,
        output_path: str,
        child_run_id: str | None,
    ) -> Dict[str, Any]:
        recorder = self.telemetry_recorder
        parent_context = recorder.current_context() if recorder is not None else None
        delegation: Dict[str, Any] = {
            "agent_name": getattr(agent, "name", name),
            "span": None,
            "spawn_event_id": None,
            "parent_context": parent_context,
            "parent_trace_id": parent_context.trace_id if parent_context else None,
        }
        if parent_context is None:
            return delegation
        actor = TelemetryActor(type=ActorType.AGENT, name=delegation["agent_name"])
        # Under governance the delegated task text is redacted like tool
        # arguments; the child still receives it.
        spawn_input = {
            "agent_name": delegation["agent_name"],
            "role": role,
            "task": "[REDACTED]" if self.governance_engine is not None else task,
            "output_path": output_path,
        }
        span = await recorder.start_span(
            name=f"subagent:{delegation['agent_name']}",
            kind="subagent.run",
            actor=actor,
            input=spawn_input,
        )
        spawn_event = await recorder.emit_event(
            "subagent_spawn",
            actor=actor,
            input=spawn_input,
            metadata={
                "subagent_span_id": span.span_id,
                "parent_trace_id": parent_context.trace_id,
                "parent_span_id": parent_context.span_id,
                "child_run_id": child_run_id,
                "dynamic": True,
            },
        )
        delegation["span"] = span
        delegation["spawn_event_id"] = spawn_event.event_id
        return delegation

    async def _finish_delegation(
        self,
        delegation: Dict[str, Any],
        *,
        child_run_id: str | None,
        child_trace_id: str | None,
        status: SpanStatus,
        error: Dict[str, Any] | None = None,
        workspace_output: Dict[str, Any] | None = None,
    ) -> None:
        if delegation["span"] is None:
            return
        await finish_delegation(
            self.telemetry_recorder,
            delegation["span"],
            agent_name=delegation["agent_name"],
            session_id=None,
            spawn_event_id=delegation["spawn_event_id"],
            parent_context=delegation["parent_context"],
            child_run_id=child_run_id,
            child_trace_id=child_trace_id,
            status=status,
            error=error,
            workspace_output=workspace_output,
        )

    @staticmethod
    def _workspace_output_error(agent: Any, output_path: str) -> str | None:
        """Return a diagnostic when a child did not create its declared output."""
        if not isinstance(output_path, str) or not output_path.strip():
            return "A non-empty workspace output path is required."

        runtime_agent = getattr(agent, "agent", None)
        tool_runtime_registry = getattr(runtime_agent, "tool_runtime_registry", None)
        workspace = getattr(tool_runtime_registry, "workspace", None)
        files = getattr(workspace, "files", None)
        if files is None or not hasattr(files, "exists"):
            return "The worker workspace was not initialized, so its output could not be verified."

        try:
            exists = files.exists(
                output_path,
                strip_prefixes=WORKSPACE_FILE_PATH_PREFIXES,
            )
        except Exception as exc:
            return f"The worker output could not be verified: {exc}"

        if not exists:
            return (
                "The worker completed without creating the requested workspace "
                f"output at '{output_path}'."
            )
        return None

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

        await self._authorize_subagent_spawns(subagent_specs)
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

    async def _authorize_subagent_spawns(
        self, subagent_specs: list[dict[str, Any]]
    ) -> None:
        # Delegation is spend like any other: a run that has used its workers
        # cannot hand out more, and the children spend the parent's budgets.
        budgets = current_budgets()
        if budgets is not None and budgets.enabled:
            await budgets.charge("subagent_runs", len(subagent_specs))
        if self.governance_engine is None:
            return
        requests = subagent_spawn_authority_requests(
            subagent_specs=subagent_specs,
            tool_names=self._local_tool_names(),
            mcp_servers=self._mcp_server_names(),
            memory_scope=str(self.agent_config.get("memory_config") or ""),
            budget=self._governance_budget_snapshot(),
        )
        await self.governance_engine.authorize_all(requests)

    def _local_tool_names(self) -> list[str]:
        if self.local_tools is None or not hasattr(self.local_tools, "list_tools"):
            return []
        return [tool.name for tool in self.local_tools.list_tools()]

    def _mcp_server_names(self) -> list[str]:
        return [str(server.get("name") or "") for server in self.mcp_tools or []]

    def _governance_budget_snapshot(self) -> dict[str, Any]:
        if (
            self.governance_engine is None
            or self.governance_engine.policy.budget is None
        ):
            return {}
        budget = self.governance_engine.policy.budget
        return {
            "max_requests": budget.max_requests,
            "max_cost": budget.max_cost,
            "used_requests": budget.used_requests,
            "used_cost": budget.used_cost,
        }

    def _governance_reference(self) -> dict[str, Any] | None:
        if self.governance_engine is None:
            return None
        return {
            "parent_policy_id": self.governance_engine.policy.policy_id,
            "parent_policy_hash": self.governance_engine.policy.provenance.policy_hash,
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
                "subagents": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 15,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "task": {"type": "string"},
                            "output_path": {"type": "string"},
                        },
                        "required": ["name", "role", "task", "output_path"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["subagents"],
            "additionalProperties": False,
        },
    )
    async def spawn_subagents(subagents: list[dict[str, Any]]) -> Dict[str, Any]:
        """Run a typed array of focused worker specifications."""
        if not isinstance(subagents, list):
            return {
                "status": "error",
                "data": None,
                "message": "subagents must be an array",
            }
        return await factory.run_parallel_subagents(subagents)
