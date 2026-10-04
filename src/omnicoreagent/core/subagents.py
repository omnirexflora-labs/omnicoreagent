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
import hashlib
import posixpath
from typing import Any, Dict, List, Optional
from omnicoreagent.core.tools.local_tools_registry import INTERNAL_TOOL_PROVIDERS, ToolRegistry
from omnicoreagent.core.logging import logger
from omnicoreagent.core.budgets import current_budgets
from omnicoreagent.core.runs import current_run
from omnicoreagent.governance.calls import current_tool_call, tool_call_metadata
from omnicoreagent.governance.capabilities import subagent_spawn_authority_requests
from omnicoreagent.governance.snapshots import derive_subagent_policy
from omnicoreagent.core.workspace.paths import WORKSPACE_FILE_PATH_PREFIXES
from omnicoreagent.core.worker_profiles import worker_profiles_from_value
from omnicoreagent.core.agents.subagent_helpers import (
    accepts_run_id,
    find_child_trace_id,
    finish_delegation,
    new_child_run_id,
)
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.telemetry.recorder import redacts_governed_arguments


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
        # The kinds of worker the lead may spawn, by name; empty when the
        # developer set none, and then every worker is as the lead.
        self.profiles = {
            profile.name: profile
            for profile in worker_profiles_from_value(self.agent_config.get("worker_profiles"))
        }

    def _build_subagent_config(
        self, *, subagent_name: str = "subagent", profile: Any = None
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
        if profile is not None:
            config["max_steps"] = profile.steps_under(config.get("max_steps", 50))
        config["enable_subagents"] = False
        config["worker_profiles"] = []
        config["enable_workspace_files"] = True
        context_management = dict(config.get("context_management") or {})
        context_management["enabled"] = True
        config["context_management"] = context_management
        tool_offload = dict(config.get("tool_offload") or {})
        tool_offload["enabled"] = True
        config["tool_offload"] = tool_offload
        if self.governance_engine is not None:
            governance_config = dict(config.get("governance_config") or {})
            # The child's policy is derived from the parent's, which already
            # carries what the parent's config said: its budgets, its policy
            # file, its profile. Saying them again would be refused.
            for key in ("budgets", "policy_path", "project_root", "profile"):
                governance_config.pop(key, None)
            governance_config.update(
                {
                    "enabled": True,
                    "policy": derive_subagent_policy(
                        self.governance_engine.policy,
                        subagent_name=subagent_name,
                        profile=profile,
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
        profile: str | None = None,
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
        chosen = self.profiles.get(profile) if profile is not None else None
        if profile is not None and chosen is None:
            raise ValueError(f"No worker profile {profile!r}; profiles: {', '.join(self.profiles)}")
        if chosen is not None and chosen.instructions:
            instruction = f"{instruction}\n{chosen.instructions.strip()}\n"

        subagent_config = self._build_subagent_config(subagent_name=name, profile=chosen)
        model_config = self.base_model_config
        mcp_tools = self.mcp_tools
        if chosen is not None:
            model_config = chosen.model_config_over(dict(self.base_model_config or {}))
            if chosen.mcp_servers is not None:
                mcp_tools = [
                    server for server in self.mcp_tools or []
                    if str(server.get("name") or "") in chosen.mcp_servers
                ]

        from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent

        agent = OmniCoreAgent(
            name=f"subagent_{name}",
            system_instruction=instruction,
            model_config=model_config,
            agent_config=subagent_config,
            mcp_tools=mcp_tools,
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

        # The worker spends the lead's budgets: one ledger for the lead and
        # its workers. It built its own from a copy of the lead's policy, keyed
        # on its own run, session and name, so three workers made nine model
        # calls under a lead limit of four (the rc7 security review).
        from omnicoreagent.core.budgets import WorkerBudgets, current_budgets

        if chosen is not None and chosen.tools is not None:
            # write_file always: a worker writes its output to a file.
            agent._only_tools = {*chosen.tools, "write_file"}
        agent.worker_profile = chosen
        lead_budgets = current_budgets()
        agent._lead_budgets = WorkerBudgets(lead_budgets) if lead_budgets is not None else None
        self._active_subagents[name] = agent
        return agent

    async def run_subagent(
        self,
        name: str,
        role: str,
        task: str,
        output_path: str,
        profile: str | None = None,
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
            profile=profile,
        )
        # A worker parked on an approval the lead has since decided resumes
        # from where it stopped instead of starting over.
        parked_run_id = self._parked_worker(name)
        if not parked_run_id:
            finished = await self._finished_worker(name, output_path)
            if finished is not None:
                return finished
        child_run_id = parked_run_id or (new_child_run_id() if accepts_run_id(agent) else None)
        lead_run = current_run()
        lead_call = current_tool_call()
        if lead_run is not None and getattr(lead_run, "enabled", False) and child_run_id and lead_call is not None:
            await lead_run.note_delegation(
                tool_call_id=lead_call.tool_call_id, name=name, child_run_id=child_run_id
            )
        delegation = await self._start_delegation(
            agent=agent,
            name=name,
            role=role,
            task=task,
            output_path=output_path,
            child_run_id=child_run_id,
        )
        child_trace_id = None
        output_before = None
        if not parked_run_id:
            output_before = await self._output_fingerprint(agent, output_path)

        try:
            if self.mcp_tools:
                await agent.connect_mcp_servers()

            if parked_run_id and hasattr(agent, "resume"):
                result = await agent.resume(parked_run_id)
            else:
                result = await agent.run(
                    str(task), **({"run_id": child_run_id} if child_run_id else {})
                )
            child_trace_id = result.get("trace_id")
            child_run_id = result.get("run_id") or child_run_id
            response = result.get("response", str(result)) or ""
            if not isinstance(response, str):
                response = str(response)

            if result.get("status") in {"awaiting_approval", "awaiting_budget"}:
                # The worker is waiting for a person. Its asks become the
                # lead's, on the lead's run, so the lead pauses too.
                return await self._park_delegation(
                    delegation, name=name, output_path=output_path, result=result,
                    child_run_id=child_run_id, child_trace_id=child_trace_id,
                )

            is_error = result.get("status", "success") != "success"

            if is_error:
                logger.warning(f"Subagent '{name}' returned an error response")
                stale = await self._stale_output_note(agent, output_path, output_before)
                if stale:
                    response = f"{response} {stale}".strip()
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
            if output_error is None:
                output_error = await self._stale_output_note(agent, output_path, output_before)
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

    def _parked_worker(self, name: str) -> str | None:
        """The run of a worker of this name whose asks the lead has decided."""
        return parked_child(current_run(), name)

    async def _finished_worker(self, name: str, output_path: str) -> Dict[str, Any] | None:
        """A worker of this name that finished before the lead paused on a
        sibling: its result, so it is not run again when the spawn call runs
        again (the rc7 gate, B7-3)."""
        child = await finished_child(current_run(), name, self.memory_router)
        if child is None:
            return None
        record, child_run_id = child
        return {
            "status": "success",
            "data": {
                "subagent_name": name,
                "output_path": output_path,
                "trace_id": (record.get("trace_ids") or [None])[-1],
                "run_id": child_run_id,
                "response": _last_assistant_text(record),
                "workspace_output": {"path": output_path},
                "governance": self._governance_reference(),
            },
            "message": f"Subagent '{name}' finished before this run paused; its output is at {output_path}.",
        }

    async def _park_delegation(
        self,
        delegation: Dict[str, Any],
        *,
        name: str,
        output_path: str,
        result: Dict[str, Any],
        child_run_id: str | None,
        child_trace_id: str | None,
    ) -> Dict[str, Any]:
        """Mirror the worker's pending asks onto the lead's run and report."""
        status = result["status"]
        mirrored = await park_child(
            current_run(),
            call=current_tool_call(),
            name=name,
            child_run_id=child_run_id,
            result=result,
            memory_router=self.memory_router,
        )
        await self._finish_delegation(
            delegation,
            child_run_id=child_run_id,
            child_trace_id=child_trace_id,
            status=SpanStatus.OK,
        )
        waiting = (
            f"{mirrored} approval(s)" if status == "awaiting_approval" else "a budget top-up"
        )
        return {
            "status": status,
            "data": {
                "subagent_name": name,
                "output_path": output_path,
                "trace_id": child_trace_id,
                "run_id": child_run_id,
                "approvals": result.get("approvals"),
                "budget_request": result.get("budget_request"),
                "governance": self._governance_reference(),
            },
            "message": (
                f"Worker '{name}' is waiting for {waiting}. This run pauses with it; "
                "when a person decides, resume this run and the worker continues."
            ),
        }

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
            "task": (
                "[REDACTED]"
                if redacts_governed_arguments(recorder, self.governance_engine is not None)
                else task
            ),
            "output_path": output_path,
        }
        profile = getattr(agent, "worker_profile", None)
        if profile is not None:
            # Which kind of worker the lead chose, and what it ran on: the
            # cost of a run is read per worker against this.
            model = getattr(agent, "model_config", None) or {}
            spawn_input.update(
                profile=profile.name,
                model=model.get("model"),
                reasoning_effort=model.get("reasoning_effort"),
            )
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
    async def _output_fingerprint(agent: Any, output_path: str) -> tuple | None:
        """What is at a worker's output path now: its content digest and
        modification time, or None when nothing is (or it cannot be read)."""
        files = await _workspace_files(agent)
        if files is None or not isinstance(output_path, str) or not output_path.strip():
            return None
        try:
            if files.exists(output_path, strip_prefixes=WORKSPACE_FILE_PATH_PREFIXES) is not True:
                return None
            content = files.read_text(output_path, strip_prefixes=WORKSPACE_FILE_PATH_PREFIXES)
            if not isinstance(content, str):
                return None
            modified = None
            parent, name = posixpath.split(output_path.rstrip("/"))
            for entry in files.list_files(parent or None, strip_prefixes=WORKSPACE_FILE_PATH_PREFIXES):
                if getattr(entry, "name", None) == name:
                    modified = getattr(entry, "modified_at", None)
                    break
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001 - a file that cannot be read is not compared.
            return None
        return (digest, str(modified))

    async def _stale_output_note(
        self, agent: Any, output_path: str, before: tuple | None
    ) -> str | None:
        """A note when the file at the output path is the one that was there
        before the worker started: an earlier run's, not this worker's."""
        if before is None:
            return None
        if await self._output_fingerprint(agent, output_path) != before:
            return None
        return (
            f"The worker did not write its output: the file at '{output_path}' is "
            "unchanged from before it started, an earlier run's, not this worker's."
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
        unknown = self._unknown_profile(subagent_specs)
        if unknown is not None:
            return {"status": "error", "data": None, "message": unknown}

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
                **({"profile": spec["profile"]} if spec.get("profile") else {}),
            )
            for i, spec in enumerate(subagent_specs)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        successful = 0
        failed = 0
        waiting: list[str] = []

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
                elif result.get("status") in {"awaiting_approval", "awaiting_budget"}:
                    waiting.append(result["status"])
                else:
                    failed += 1

        return {
            "status": waiting[0]
            if waiting
            else "success"
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

    def _unknown_profile(self, subagent_specs: list[dict[str, Any]]) -> str | None:
        """Why no worker can start, when one names no profile the developer
        set: none is started, so the lead can fix its call and spawn again."""
        if not getattr(self, "profiles", None):
            return None
        names = ", ".join(self.profiles)
        for spec in subagent_specs:
            profile = spec.get("profile")
            if profile not in self.profiles:
                given = "no profile" if profile is None else f"profile {profile!r}"
                return (
                    f"Worker {spec.get('name')!r} has {given}. Each worker needs one of: "
                    f"{names}. No worker was started."
                )
        return None

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
        # An ask on delegation is recorded against the spawn call, so the
        # run pauses on it and continues the call after a decision.
        for request in requests:
            request.metadata = {**tool_call_metadata(), **(request.metadata or {})}
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


_SPAWN_DESCRIPTION = """
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
        """


def _profiles_description(factory: SubagentFactory) -> str:
    """The profiles, as the lead reads them to choose a worker."""
    lead_model = factory.base_model_config or {}
    lines = ["", "    Worker profiles: give each worker the `profile` that fits its task.", ""]
    for profile in factory.profiles.values():
        model = profile.model_config_over(dict(lead_model))
        facts = [f"model {model.get('model')}"]
        if model.get("reasoning_effort"):
            facts.append(f"effort {model['reasoning_effort']}")
        if profile.max_steps is not None:
            facts.append(f"at most {profile.max_steps} steps")
        if profile.tools is not None:
            facts.append("tools: " + ", ".join(profile.tools))
        lines.append(f"    - {profile.name}: {profile.description.strip()} ({'; '.join(facts)})")
    return "\n".join(lines) + "\n"


def build_subagent_tools(
    factory: SubagentFactory,
    registry: ToolRegistry,
) -> None:
    """
    Register subagent spawning tools with the given registry.
    """

    item_properties: dict[str, Any] = {
        "name": {"type": "string"},
        "role": {"type": "string"},
        "task": {"type": "string"},
        "output_path": {"type": "string"},
    }
    required = ["name", "role", "task", "output_path"]
    description = _SPAWN_DESCRIPTION
    profiles = getattr(factory, "profiles", None) or {}
    if profiles:
        item_properties["profile"] = {
            "type": "string",
            "enum": list(profiles),
            "description": "The kind of worker: one of the profiles listed in this tool's description.",
        }
        required.append("profile")
        description += _profiles_description(factory)

    @registry.register_tool(
        name="spawn_subagents",
        description=description,
        inputSchema={
            "type": "object",
            "properties": {
                "subagents": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 15,
                    "items": {
                        "type": "object",
                        "properties": item_properties,
                        "required": required,
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


async def _workspace_files(agent: Any) -> Any:
    """The worker's workspace files, initializing the worker if it has not been."""
    initialize = getattr(agent, "initialize", None)
    if getattr(agent, "_initialized", True) is False and callable(initialize):
        try:
            await initialize()
        except Exception:  # noqa: BLE001 - run() reports a worker that cannot start.
            return None
    runtime_agent = getattr(agent, "agent", None)
    registry = getattr(runtime_agent, "tool_runtime_registry", None)
    workspace = getattr(registry, "workspace", None)
    create = getattr(registry, "_workspace_for_runtime_tools", None)
    if workspace is None and callable(create):
        # Made when the worker's tools are prepared; the same one, earlier.
        try:
            workspace = create()
        except Exception:  # noqa: BLE001 - a workspace that cannot be made is not compared.
            return None
    files = getattr(workspace, "files", None)
    if files is None or not hasattr(files, "read_text"):
        return None
    return files


# --- Pausing with a child, shared by spawned workers and named children -------


async def park_child(run, *, call, name: str, child_run_id: str | None, result: dict, memory_router) -> int:
    """Mirror a child's pending asks (approvals, a budget request) onto the
    lead's run, so the lead pauses with the child, a decision reaches the
    child's, and the lead's delegation resumes the child. Returns how many
    were mirrored. A named child's ask was returned to the lead as a
    successful result and the child left waiting (the rc7 gate, B7-1)."""
    status = result.get("status")
    if run is None or not getattr(run, "enabled", False) or not child_run_id or memory_router is None:
        return 0
    try:
        child_record = await memory_router.get_run_state(child_run_id)
    except Exception:  # noqa: BLE001 - a store without run state
        return 0
    if child_record is None:
        return 0
    call_id = call.tool_call_id if call is not None else None
    mirrored = 0
    if status == "awaiting_approval":
        already = {a.get("delegated_approval_id") for a in run.record.get("approvals") or []}
        public = {a.get("approval_id"): a for a in result.get("approvals") or []}
        for entry in child_record.get("approvals") or []:
            if entry.get("status") != "pending" or entry["approval_id"] in already:
                continue
            shown = public.get(entry["approval_id"]) or {}
            await run.add_approval(
                {
                    **entry,
                    "approval_id": f"approval_{__import__('uuid').uuid4().hex}",
                    "tool_call_id": call_id,
                    "arguments": shown.get("arguments"),
                    "delegated_run_id": child_run_id,
                    "delegated_approval_id": entry["approval_id"],
                    "delegated_name": name,
                }
            )
            mirrored += 1
    if status == "awaiting_budget":
        already = {
            d.get("request_id")
            for r in run.record.get("budget_requests") or []
            for d in r.get("delegated") or []
        }
        for entry in child_record.get("budget_requests") or []:
            if entry.get("status") != "pending" or entry["request_id"] in already:
                continue
            await run.add_budget_request(
                {
                    **entry,
                    "request_id": f"budgetreq_{__import__('uuid').uuid4().hex}",
                    "for": call_id or entry.get("for"),
                    "delegated_run_id": child_run_id,
                    "delegated_request_id": entry["request_id"],
                    "delegated_name": name,
                }
            )
            mirrored += 1
    return mirrored


def parked_child(run, name: str) -> str | None:
    """The run of a child of this name whose asks the lead has decided."""
    if run is None or not getattr(run, "enabled", False):
        return None
    for approval in reversed(run.record.get("approvals") or []):
        if (
            approval.get("delegated_name") == name
            and approval.get("delegated_run_id")
            and approval.get("status") in {"approved", "denied", "used"}
        ):
            return approval["delegated_run_id"]
    for request in reversed(run.record.get("budget_requests") or []):
        if request.get("status") not in {"granted", "denied"}:
            continue
        for waiting in request.get("delegated") or []:
            if waiting.get("name") == name:
                return waiting["run_id"]
    return None


async def finished_child(run, name: str, memory_router):
    """A child of this name this call already ran to completion: its record
    and run id, or None."""
    call = current_tool_call()
    if run is None or not getattr(run, "enabled", False) or call is None or memory_router is None:
        return None
    for noted in reversed(run.record.get("delegations") or []):
        if noted["name"] == name and noted["tool_call_id"] == call.tool_call_id:
            try:
                record = await memory_router.get_run_state(noted["child_run_id"])
            except Exception:  # noqa: BLE001
                return None
            if record is not None and record.get("status") == "completed":
                return record, noted["child_run_id"]
            return None
    return None


def _last_assistant_text(record: dict) -> str:
    for message in reversed((record.get("context") or {}).get("messages") or []):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""
