from __future__ import annotations

import asyncio
import hashlib
import json
import time

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from omnicoreagent.core.system_prompts import AgentPromptContextBuilder
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimits,
)
from omnicoreagent.core.types import (
    AgentState,
    Message,
    SessionState,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.governed_tool_runner import GovernedToolRunner
from omnicoreagent.core.budgets import BudgetExhaustedForRun, RunAwaitingBudget
from omnicoreagent.core.runs import RunInterrupted, RunSuspended, current_run
from omnicoreagent.core.tools.tool_runtime_registry import ToolRuntimeRegistry
from omnicoreagent.core.telemetry import ActorType, SpanStatus, TelemetryActor
from omnicoreagent.core.logging import logger
from omnicoreagent.core.context_manager import (
    AgentLoopContextManager,
    ContextManagementConfig,
)
from omnicoreagent.core.workspace.artifacts import (
    ToolResponseOffloader,
    OffloadConfig,
)
from omnicoreagent.core.agents.initial_messages import AgentInitialMessagePreparer
from omnicoreagent.core.agents.llm_step import AgentLlmStepRunner
from omnicoreagent.core.agents.native_tools import execute_native_turn
from omnicoreagent.core.model_protocol import ModelTurn
from omnicoreagent.core.tools.native_catalog import NativeToolCatalog
from omnicoreagent.core.agents.message_history import AgentMessageHistoryLoader
from omnicoreagent.core.agents.run_outcome import AgentRunOutcomeHandler
from omnicoreagent.core.agents.session_state import AgentSessionStateStore
from omnicoreagent.core.agents.subagent_runner import SubAgentCallRunner
from omnicoreagent.core.tools.tool_result_offloader import ToolResultOffloader
from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.interaction_history import context_evidence, stable_message_digest


if TYPE_CHECKING:
    from omnicoreagent.core.guardrails import PromptInjectionGuard
    from omnicoreagent.core.workspace.config import WorkspaceConfig


def _sandbox_execution(governance_engine: Any):
    """A governed execution service when the configured sandbox can execute."""
    runtime = getattr(governance_engine, "sandbox_runtime", None)
    if (
        governance_engine is None
        or not getattr(runtime, "supports_execution", False)
        or not governance_engine._sandbox_runtime_satisfies_required_boundary()
    ):
        return None
    from omnicoreagent.sandbox import SandboxExecutionService

    return SandboxExecutionService(governance_engine)


class BaseReactAgent:
    """Autonomous agent implementing the ReAct paradigm for task solving through iterative reasoning and tool usage."""

    def __init__(
        self,
        agent_name: str,
        max_steps: int,
        tool_call_timeout: int,
        subagent_timeout: int | None = None,
        request_limit: int = 0,
        total_tokens_limit: int = 0,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_files: bool = False,
        enable_agent_skills: bool = False,
        skill_script_env: list[str] | None = None,
        code_mode: dict[str, Any] | None = None,
        agents_md: dict[str, Any] | None = None,
        context_management_config: dict = None,
        tool_offload_config: dict = None,
        workspace_config: WorkspaceConfig | dict | None = None,
        guardrail: PromptInjectionGuard | None = None,
        governance_engine: Any = None,
        privacy_filter: PrivacyFilter | None = None,
    ):
        self.agent_name = agent_name
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.max_steps = max_steps
        self.tool_call_timeout = tool_call_timeout
        # A delegation's bound, or None: a worker is not one tool call.
        self.subagent_timeout = subagent_timeout

        self.request_limit = request_limit
        self.total_tokens_limit = total_tokens_limit
        self._limits_enabled = request_limit > 0 or total_tokens_limit > 0
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents
        self.enable_workspace_files = enable_workspace_files or enable_subagents
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = None
        from omnicoreagent.core.tools.code_mode import CodeModeConfig

        self.code_mode = CodeModeConfig.from_value(code_mode)
        from omnicoreagent.core.project_instructions import ProjectInstructionsConfig

        self.agents_md = ProjectInstructionsConfig.from_value(agents_md)
        self.workspace_config = workspace_config
        self.usage_limits = UsageLimits(
            request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit
        )

        self.session_state_store = AgentSessionStateStore(agent_name=self.agent_name)
        self._session_states = self.session_state_store.states
        self.init_skills()
        self.register_internal_tool = ToolRegistry()

        self.context_manager = AgentLoopContextManager(
            ContextManagementConfig.from_dict(context_management_config or {})
        )
        self.llm_step_runner = AgentLlmStepRunner(
            agent_name=self.agent_name,
            context_manager=self.context_manager,
            usage_limits=self.usage_limits,
            limits_enabled=self._limits_enabled,
            request_limit=self.request_limit,
        )

        self.tool_offloader = ToolResponseOffloader(
            config=OffloadConfig.from_dict(tool_offload_config or {}),
            workspace_config=workspace_config,
            privacy_filter=privacy_filter,
        )
        self.guardrail = guardrail
        self.governance_engine = governance_engine
        # A result moved to the workspace is read back with read_artifact; a
        # policy that refuses that would take the result from the model. Keep
        # it inline instead (the context manager still bounds the context).
        self.tool_offload_refused_by_policy = bool(
            self.tool_offloader.config.enabled
            and _artifact_reads_refused(governance_engine, self.agent_name)
        )
        if self.tool_offload_refused_by_policy:
            self.tool_offloader.config.enabled = False
        self.tool_result_offloader = ToolResultOffloader(self.tool_offloader)
        self.message_history_loader = AgentMessageHistoryLoader(
            agent_name=self.agent_name
        )
        self.subagent_runner = SubAgentCallRunner(agent_name=self.agent_name)
        self.governed_tool_runner = GovernedToolRunner(
            agent_name=self.agent_name,
            governance_engine=self.governance_engine,
        )
        # The governed route to a sandbox that can execute, or None. With it,
        # the `execute` tool is offered and skill scripts run in the sandbox.
        self.sandbox_execution = _sandbox_execution(self.governance_engine)
        self.tool_runtime_registry = ToolRuntimeRegistry(
            register_internal_tool=self.register_internal_tool,
            tool_offloader=self.tool_offloader,
            sandbox_execution=self.sandbox_execution,
            tool_call_timeout=tool_call_timeout,
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_files=self.enable_workspace_files,
            enable_agent_skills=self.enable_agent_skills,
            skill_manager=self.skill_manager,
            skill_script_env=skill_script_env,
            code_mode=self.code_mode,
            workspace_config=workspace_config,
            privacy_filter=privacy_filter,
        )
        self.prompt_context_builder = AgentPromptContextBuilder(
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_files=self.enable_workspace_files,
            enable_agent_skills=self.enable_agent_skills,
            is_tool_offload_enabled=lambda: self.tool_offloader.config.enabled,
            skill_manager=self.skill_manager,
        )
        self.initial_message_preparer = AgentInitialMessagePreparer(
            message_history_loader=self.message_history_loader,
            prompt_context_builder=self.prompt_context_builder,
        )
        self.run_outcome_handler = AgentRunOutcomeHandler(agent_name=self.agent_name)

    def init_skills(self):
        if self.enable_agent_skills:
            from omnicoreagent.core.skills.manager import SkillManager

            self.skill_manager = SkillManager()
            self.skill_manager.discover_skills()
            logger.info(
                f"Agent Skills enabled: found {len(self.skill_manager.skills)} skills"
            )

    def _get_session_state(self, session_id: str, debug: bool) -> SessionState:
        return self.session_state_store.get(session_id=session_id, debug=debug)

    async def reset_system_prompt(self, messages: list, system_prompt: str):
        old_messages = messages[1:]
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(old_messages)
        return messages

    def agent_session_state_context(
        self, new_state: AgentState, session_id: str, debug: bool
    ):
        """Context manager to change the agent session state"""
        return self.session_state_store.state_context(
            new_state=new_state,
            session_id=session_id,
            debug=debug,
        )

    async def _record_runtime_message(
        self,
        telemetry_recorder: Any,
        message: Any,
        *,
        kind: str,
        content: str | None = None,
    ) -> None:
        """Record text the runtime added to the model context.

        ``message_digest`` equals the digest the next context assembly records
        for this message, so the two can be matched. The text is harness text,
        recorded in metadata under every capture policy.
        """
        if telemetry_recorder is None:
            return
        await telemetry_recorder.emit_event(
            "runtime_message",
            actor=TelemetryActor(type=ActorType.SYSTEM, name=self.agent_name),
            metadata={
                "kind": kind,
                "role": getattr(message, "role", None),
                "content": content if content is not None else message.content,
                "message_digest": stable_message_digest(
                    message, canonicalizer=telemetry_recorder.canonicalize_for_digest
                ),
            },
        )

    async def _record_run_configuration(
        self,
        telemetry_recorder: Any,
        *,
        header: dict[str, Any] | None,
        messages: list[Any],
        catalog: NativeToolCatalog,
    ) -> None:
        """Record the harness the model runs with, before its first step.

        Digests use the same privacy-safe canonical form as each step's
        context evidence, so the tool schema digest equals the first step's
        ``tool_catalog_digest`` while the catalog is unchanged. The system
        prompt text is recorded only when model prompt capture is enabled.
        """
        system_messages = [
            message for message in messages if getattr(message, "role", None) == "system"
        ]
        system_prompt = "\n\n".join(
            str(getattr(message, "content", "") or "") for message in system_messages
        )
        definitions = catalog.definitions()
        evidence = context_evidence(
            system_messages,
            definitions,
            canonicalizer=telemetry_recorder.canonicalize_for_digest,
        )
        prompt_digest = hashlib.sha256(
            "".join(evidence["message_digests"]).encode("utf-8")
        ).hexdigest()
        providers: dict[str, int] = {}
        for key in catalog.visible:
            binding = catalog.bindings.get(key)
            if binding is not None:
                provider = str(getattr(binding, "provider", "unknown"))
                providers[provider] = providers.get(provider, 0) + 1
        configuration = dict(header or {})
        configuration["tools"] = {
            "count": evidence["tool_count"],
            "names": evidence["tool_names"],
            "by_provider": dict(sorted(providers.items())),
            "schema_digest": evidence["tool_catalog_digest"],
        }
        configuration["system_prompt"] = {
            "digest": prompt_digest,
            "bytes": len(system_prompt.encode("utf-8")),
            "message_digests": evidence["message_digests"],
        }
        agent = dict(configuration.get("agent") or {"name": self.agent_name})
        if not agent.get("version"):
            harness = {key: value for key, value in configuration.items() if key != "agent"}
            agent["version"] = hashlib.sha256(
                json.dumps(harness, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
        configuration["agent"] = agent
        # Configuration is metadata, recorded under every capture policy; only
        # the prompt text itself follows the model prompt policy.
        await telemetry_recorder.emit_event(
            "run_configuration",
            actor=TelemetryActor(type=ActorType.AGENT, name=self.agent_name),
            input={"system_prompt": system_prompt},
            metadata={"run_configuration": configuration},
        )
        memory_config = {
            key: configuration.get(key)
            for key in ("memory", "context_management", "tool_offload")
            if key in configuration
        }
        versions = {
            "agent_version": agent["version"],
            "prompt_version": prompt_digest[:16],
            "tool_schema_version": evidence["tool_catalog_digest"][:16],
        }
        if memory_config:
            versions["memory_config_version"] = hashlib.sha256(
                json.dumps(memory_config, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:16]
        await telemetry_recorder.update_trace_metadata(versions)

    async def run(self, *args, **kwargs) -> Any:
        """Run one turn; a sandbox session opened by it is closed when it ends."""
        if self.sandbox_execution is None:
            return await self._run(*args, **kwargs)
        from omnicoreagent.sandbox.scope import ExecutionScope

        scope = ExecutionScope(
            self.sandbox_execution,
            getattr(self.governance_engine, "sandbox_manifest", None),
            workspace_bridge=self._workspace_bridge(),
        )
        async with scope.active():
            return await self._run(*args, **kwargs)

    def _workspace_bridge(self):
        """Workspace files for the run's sandbox, when workspace files are enabled."""
        if not self.enable_workspace_files:
            return None
        from omnicoreagent.sandbox.workspace_bridge import WorkspaceBridge

        registry = self.tool_runtime_registry
        return WorkspaceBridge(
            registry._workspace_for_runtime_tools().files,
            governance_engine=self.governance_engine,
            privacy_filter=registry.privacy_filter,
        )

    def _project_instructions(self):
        """The project's instructions for this run (AGENTS.md), if configured."""
        from omnicoreagent.core.project_instructions import load_project_instructions
        from omnicoreagent.core.workspace.config import resolve_workspace_config

        if not self.agents_md.enabled:
            from omnicoreagent.core.project_instructions import ProjectInstructions

            return ProjectInstructions()
        workspace = resolve_workspace_config(self.workspace_config)
        return load_project_instructions(
            self.agents_md,
            workspace_dir=workspace.workspace_dir if workspace.workspace_backend == "local" else None,
            guardrail=self.guardrail,
        )

    async def _deliver_steering(
        self,
        message: dict[str, Any],
        *,
        session_state: SessionState,
        session_id: str,
        add_message_to_history: Callable,
        telemetry_recorder: Any,
    ) -> None:
        """A steering message becomes the next user message of the run."""
        content = message["content"]
        session_state.messages.append(Message(role="user", content=content))
        await add_message_to_history(
            role="user",
            content=content,
            session_id=session_id,
            metadata={
                "agent_name": self.agent_name,
                "kind": "steering",
                "steer_id": message["id"],
                "sender": message.get("sender"),
            },
        )
        if telemetry_recorder is not None:
            await telemetry_recorder.emit_event(
                "run_steered",
                actor=TelemetryActor(type=ActorType.USER, name=message.get("sender")),
                input={"message": content},
                metadata={"steer_id": message["id"], "sender": message.get("sender")},
            )

    async def _run(
        self,
        system_prompt: str,
        query: str,
        llm_connection: Callable,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        message_history: Callable[[], Any],
        debug: bool = False,
        sessions: dict = None,
        mcp_tools: dict = None,
        local_tools: Any = None,
        session_id: str = None,
        telemetry_recorder: Any = None,
        sub_agents: list = None,
        on_event: Any = None,
        telemetry_run_header: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
    ) -> Any:
        """Run native model turns, correlated tool results and final text.

        ``resume`` is a paused run's record: the run continues from its own
        saved context, first running the calls that were waiting for approval.
        """
        session_state = self.session_state_store.reset_for_run(
            session_id=session_id, debug=debug
        )
        start_time = time.perf_counter()
        run_usage = Usage()

        runtime_local_tools = await self.tool_runtime_registry.prepare_tools(
            local_tools=local_tools
        )
        catalog = NativeToolCatalog(
            local_tools=runtime_local_tools,
            mcp_tools=mcp_tools,
            sub_agents=sub_agents,
            advanced=self.enable_advanced_tool_use,
        )
        if resume is not None:
            # The run's own context, never the shared session history.
            saved = resume["context"]
            saved_messages = [*(saved.get("history") or []), *saved["messages"]]

            async def message_history(**_):
                return [dict(message) for message in saved_messages]

        # A project's own instructions, read fresh for this run.
        project_instructions = self._project_instructions()
        await self.initial_message_preparer.prepare(
            system_prompt=system_prompt,
            session_state=session_state,
            message_history=message_history,
            catalog=catalog,
            session_id=session_id,
            project_instructions=project_instructions.text,
            # A resumed run answers the calls its paused turn made.
            keep_pending_tool_calls=resume is not None,
        )
        if telemetry_recorder is not None:
            if project_instructions.files or project_instructions.skipped:
                telemetry_run_header = {
                    **(telemetry_run_header or {}),
                    "project_instructions": project_instructions.header(),
                }
            await self._record_run_configuration(
                telemetry_recorder,
                header=telemetry_run_header,
                messages=session_state.messages,
                catalog=catalog,
            )
        if resume is None:
            session_state.messages.append(Message(role="user", content=query))
            self.prompt_context_builder.inject_current_datetime(session_state.messages)
            context_prefix = _datetime_prefix(session_state.messages[-1], query)
            await self._record_runtime_message(
                telemetry_recorder,
                session_state.messages[-1],
                kind="current_datetime",
                content=context_prefix,
            )

            # History keeps the query itself; the prefix is stored beside it so a
            # later run resends this message exactly as the model first saw it,
            # which keeps the provider's prompt cache prefix intact.
            await add_message_to_history(
                role="user",
                content=query,
                session_id=session_id,
                metadata={"agent_name": self.agent_name, "context_prefix": context_prefix},
            )
        if session_state.state not in [
            AgentState.IDLE,
            AgentState.ERROR,
        ]:
            raise RuntimeError(
                f"Agent is not in a valid state to run: {session_state.state}"
            )

        async with self.agent_session_state_context(
            new_state=AgentState.RUNNING, session_id=session_id, debug=debug
        ):
            current_steps = 0
            if resume is not None:
                current_steps = int(resume.get("step") or 0)
                pending, unknown = _pending_calls(resume, catalog)
                if pending:
                    await execute_native_turn(
                        self,
                        turn=ModelTurn(tool_calls=tuple(pending), finish_reason="tool_calls"),
                        unknown_outcome_ids=unknown,
                        catalog=catalog,
                        local_tools=runtime_local_tools,
                        sessions=sessions,
                        session_state=session_state,
                        session_id=session_id,
                        add_message_to_history=add_message_to_history,
                        run_usage=run_usage,
                        telemetry_recorder=telemetry_recorder,
                        resuming=True,
                    )
            if resume is not None and resume.get("sandbox_used"):
                # After the paused step's results, never between a tool call
                # and its result.
                notice = Message(role="user", content=SANDBOX_RESET_NOTICE)
                session_state.messages.append(notice)
                await self._record_runtime_message(
                    telemetry_recorder, notice, kind="sandbox_reset"
                )
            while (
                session_state.state not in [AgentState.FINISHED]
                and current_steps < self.max_steps
            ):
                run = current_run()
                if run is not None:
                    # Step boundary: take messages steered to this run, and
                    # stop here if someone asked the run to.
                    steered, stop = await run.check_external()
                    if stop:
                        interrupted = RunInterrupted("Run interrupted at a step boundary")
                        interrupted.usage = run_usage
                        raise interrupted
                    for message in steered:
                        await self._deliver_steering(
                            message,
                            session_state=session_state,
                            session_id=session_id,
                            add_message_to_history=add_message_to_history,
                            telemetry_recorder=telemetry_recorder,
                        )
                current_steps += 1
                if run is not None:
                    await run.step(current_steps)
                step_span = None
                if telemetry_recorder is not None:
                    step_span = await telemetry_recorder.start_span(
                        name="agent.step",
                        kind="agent.step",
                        actor=TelemetryActor(
                            type=ActorType.AGENT, name=self.agent_name
                        ),
                        input={"step": current_steps},
                    )
                    await telemetry_recorder.emit_event(
                        "agent_step",
                        actor=TelemetryActor(
                            type=ActorType.AGENT, name=self.agent_name
                        ),
                        input={"step": current_steps},
                    )
                try:
                    llm_step = await self.llm_step_runner.run(
                        session_state=session_state,
                        llm_connection=llm_connection,
                        on_event=on_event,
                        tools=[]
                        if session_state.state == AgentState.STUCK
                        else catalog.definitions(),
                        run_usage=run_usage,
                        session_id=session_id,
                        telemetry_recorder=telemetry_recorder,
                        debug=debug,
                    )
                    if llm_step.error_result is not None:
                        if telemetry_recorder is not None and step_span is not None:
                            await telemetry_recorder.end_span(
                                step_span.span_id,
                                status=SpanStatus.ERROR,
                                output={"error_result": llm_step.error_result},
                            )
                        return llm_step.error_result
                    turn = llm_step.response
                    if turn is None:
                        raise ValueError("Model returned no turn")
                    if turn.refusal or turn.finish_reason in {
                        "length",
                        "content_filter",
                    }:
                        if telemetry_recorder is not None and step_span is not None:
                            await telemetry_recorder.end_span(
                                step_span.span_id, status=SpanStatus.ERROR
                            )
                        run_usage.total_time = time.perf_counter() - start_time
                        return {
                            "answer": turn.refusal or turn.text,
                            "usage": run_usage,
                            "status": "error",
                            "termination_reason": turn.finish_reason or "refusal",
                        }
                    if turn.tool_calls:
                        if session_state.state == AgentState.STUCK:
                            if telemetry_recorder is not None and step_span is not None:
                                await telemetry_recorder.end_span(
                                    step_span.span_id, status=SpanStatus.ERROR
                                )
                            run_usage.total_time = time.perf_counter() - start_time
                            return {
                                "answer": "Repeated tool calls halted.",
                                "usage": run_usage,
                                "status": "error",
                                "termination_reason": "tool_loop",
                            }
                        await execute_native_turn(
                            self,
                            turn=turn,
                            catalog=catalog,
                            local_tools=runtime_local_tools,
                            sessions=sessions,
                            session_state=session_state,
                            session_id=session_id,
                            add_message_to_history=add_message_to_history,
                            run_usage=run_usage,
                            telemetry_recorder=telemetry_recorder,
                            model_call_span_id=llm_step.model_call_span_id,
                            model_call_event_id=llm_step.model_call_event_id,
                            model_response_event_id=llm_step.model_response_event_id,
                            agent_step_span_id=(
                                step_span.span_id if step_span is not None else None
                            ),
                        )
                        if session_state.loop_detector.is_looping():
                            session_state.state = AgentState.STUCK
                            recovery = Message(
                                role="user",
                                content="Repeated tool calls are not making progress. Give your best answer with the available results and explain remaining limitations. Tools are disabled.",
                            )
                            session_state.messages.append(recovery)
                            await self._record_runtime_message(
                                telemetry_recorder, recovery, kind="loop_recovery"
                            )
                    elif turn.text.strip():
                        if telemetry_recorder is not None and step_span is not None:
                            await telemetry_recorder.end_span(
                                step_span.span_id,
                                status=SpanStatus.OK,
                                output={"returned": True},
                            )
                        return await self.run_outcome_handler.handle_final_answer(
                            answer=turn.text,
                            session_state=session_state,
                            add_message_to_history=add_message_to_history,
                            session_id=session_id,
                            run_usage=run_usage,
                            start_time=start_time,
                        )
                    else:
                        retry = Message(
                            role="user",
                            content="The previous response was empty. Provide an answer or use an available tool.",
                        )
                        session_state.messages.append(retry)
                        await self._record_runtime_message(
                            telemetry_recorder, retry, kind="empty_response_retry"
                        )
                    if telemetry_recorder is not None and step_span is not None:
                        await telemetry_recorder.end_span(
                            step_span.span_id,
                            status=SpanStatus.OK,
                            output={"returned": False},
                        )
                except BudgetExhaustedForRun as spent:
                    # A budget covering this run is gone. The work already done
                    # stands; the run stops and says which budget ran out.
                    if telemetry_recorder is not None and step_span is not None:
                        await telemetry_recorder.end_span(
                            step_span.span_id,
                            status=SpanStatus.OK,
                            output={"budget_exhausted": spent.scope},
                        )
                    run_usage.total_time = time.perf_counter() - start_time
                    return {
                        "answer": (
                            f"This run stopped because the {spent.scope} budget is "
                            f"exhausted: {spent}"
                        ),
                        "usage": run_usage,
                        "status": "error",
                        "termination_reason": "budget_exhausted",
                    }
                except RunAwaitingBudget as waiting:
                    # Waiting for a person to top up a budget is not a failure
                    # of the step: the work done so far is kept.
                    waiting.usage = run_usage
                    if telemetry_recorder is not None and step_span is not None:
                        await telemetry_recorder.end_span(
                            step_span.span_id,
                            status=SpanStatus.OK,
                            output={"awaiting_budget": waiting.request["meter"]},
                        )
                    raise
                except RunSuspended as suspended:
                    # Waiting for a person is not a failure of the step.
                    suspended.usage = run_usage
                    if telemetry_recorder is not None and step_span is not None:
                        await telemetry_recorder.end_span(
                            step_span.span_id,
                            status=SpanStatus.OK,
                            output={"suspended": True},
                        )
                    raise
                except BaseException as exc:
                    if telemetry_recorder is not None and step_span is not None:
                        await telemetry_recorder.end_span(
                            step_span.span_id,
                            status=SpanStatus.CANCELLED
                            if isinstance(exc, asyncio.CancelledError)
                            else SpanStatus.ERROR,
                            error={
                                "type": exc.__class__.__name__,
                                "message": str(exc),
                            },
                        )
                    raise

        run_usage.total_time = time.perf_counter() - start_time
        return {
            "answer": "Agent reached its step limit.",
            "usage": run_usage,
            "status": "error",
            "termination_reason": "max_steps",
        }


def _datetime_prefix(message: Any, query: str) -> str:
    """The text the runtime prepended to the user's query."""
    content = str(getattr(message, "content", "") or "")
    return content[: len(content) - len(query)] if content.endswith(query) else content


SANDBOX_RESET_NOTICE = (
    "This run was paused and has now resumed. Its sandbox was reset: files "
    "outside the workspace (for example in /tmp), installed packages, and "
    "running processes from before the pause are gone. The workspace files are "
    "intact."
)


def _pending_calls(record: dict[str, Any], catalog: Any) -> tuple[list, set[str]]:
    """The calls of the stopped step that have no result yet, and which of
    them have an unknown outcome.

    A call that never started runs. A call waiting for approval runs through
    governance, which applies the recorded decision (with edited arguments if
    the approver changed them). A call that started but never finished (the
    process stopped) runs again only if its tool is idempotent; otherwise its
    outcome is unknown and the model is told so instead.
    """
    from omnicoreagent.core.model_protocol import ToolRequest

    messages = record["context"]["messages"]
    turn_index = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i]["role"] == "assistant"
            and (messages[i].get("metadata") or {}).get("tool_calls")
        ),
        None,
    )
    if turn_index is None:
        return [], set()
    answered = {
        (m.get("metadata") or {}).get("tool_call_id") for m in messages[turn_index + 1 :]
    }
    edited = {
        approval.get("tool_call_id"): approval["edited_arguments"]
        for approval in record.get("approvals", [])
        if approval.get("edited_arguments") is not None
    }
    states = {c["tool_call_id"]: c["state"] for c in record.get("tool_calls", [])}
    pending, unknown = [], set()
    for call in messages[turn_index]["metadata"]["tool_calls"]:
        if call["id"] in answered:
            continue
        function = call.get("function") or {}
        arguments = function.get("arguments") or "{}"
        if call["id"] in edited:
            arguments = json.dumps(edited[call["id"]])
        pending.append(ToolRequest(call["id"], function.get("name"), arguments))
        if states.get(call["id"]) in {"started", "interrupted"}:
            binding = catalog.bindings.get(str(function.get("name")).lower())
            if binding is None or not binding.idempotent:
                unknown.add(call["id"])
    return pending, unknown


def _artifact_reads_refused(engine: Any, actor: str) -> bool:
    """Whether governance would refuse the tool an offloaded result is read with."""
    if engine is None:
        return False
    from omnicoreagent.governance.capabilities import tool_authority_requests
    from omnicoreagent.governance.models import PolicyEffect

    requests = tool_authority_requests(
        tool_name="read_artifact",
        tool_args={"artifact_id": "offloaded"},
        tool_provider="artifact",
        actor=actor,
    )
    try:
        decisions = [engine.evaluator.evaluate(engine.policy, request) for request in requests]
    except Exception:  # noqa: BLE001 - a policy that cannot say is treated as refusing.
        return True
    return any(decision.effect == PolicyEffect.DENY for decision in decisions)
