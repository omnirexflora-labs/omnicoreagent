from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from omnicoreagent.core.runs import (
    RunStateUnsupported,
    RunTracker,
    current_run,
    supports_run_state,
)
from omnicoreagent.core.runtime import (
    builder,
    construction,
    execution,
    harness_tools,
    normalization,
    summaries,
    streaming,
)
from omnicoreagent.core.privacy import PrivacyFilter
from omnicoreagent.core.interaction_history import stable_message_digest
from omnicoreagent.core.runtime.deadline import (
    complete_despite_cancellation,
    current_stop_reason,
)
from omnicoreagent.core.telemetry.payloads import payload_references
from omnicoreagent.core.telemetry.summary import (
    final_model_response_event_id,
    summarize_trace,
)
from omnicoreagent.core.telemetry.trajectory import build_trajectory
from omnicoreagent.core.runtime.imports import (
    LazyDefaultPromptBuilder,
    runtime,
    runtime_logger,
)
from omnicoreagent.core.telemetry import (
    ActorType,
    TelemetryActor,
    TelemetryContext,
    TelemetryExporter,
    TelemetryTrace,
    TelemetryNormalizer,
    TelemetryRecorder,
    TelemetryStream,
    TelemetryStreamScope,
    TelemetryConfig,
    TraceFilter,
    TraceStatus,
    build_telemetry_exporter,
    export_trace_to_many,
    set_telemetry_context,
)



def _without_changed_continuation(original: Any, redacted: Any) -> Any:
    """Never store continuation data that privacy redaction changed.

    A provider signs its thinking text, so a redacted copy would be rejected
    on a later request. When memory redaction must change it, that turn's
    continuation data is not stored and ``continuation_dropped`` says which
    fields were left out; the live run keeps its own unredacted copy.
    """
    if not isinstance(original, dict) or not isinstance(redacted, dict):
        return redacted
    source = original.get("model_message")
    stored = redacted.get("model_message")
    if not isinstance(source, dict) or not isinstance(stored, dict):
        return redacted
    from omnicoreagent.core.agents.llm_response import CONTINUATION_FIELDS

    dropped = [
        key
        for key in CONTINUATION_FIELDS
        if key in stored and key != "reasoning_content" and stored[key] != source.get(key)
    ]
    if not dropped:
        return redacted
    stored = {key: value for key, value in stored.items() if key not in dropped}
    return {**redacted, "model_message": stored, "continuation_dropped": dropped}

class OmniCoreAgent:
    """
    Public facade for the OmniCoreAgent runtime.

    The facade owns lifecycle, session, memory, telemetry, and execution APIs while
    delegating construction details to runtime helper modules.
    """

    def __init__(
        self,
        name: str,
        system_instruction: str,
        model_config: Any,
        mcp_tools: Optional[List[Any]] = None,
        local_tools: Optional[Any] = None,
        sub_agents: Optional[Dict[str, Any]] = None,
        agent_config: Optional[Any] = None,
        memory_router: Optional[Any] = None,
        telemetry_store: Optional[Any] = None,
        telemetry_recorder: Optional[Any] = None,
        telemetry_stream: Optional[Any] = None,
        telemetry_exporters: Optional[List[Any]] = None,
        prompt_builder: Optional[Any] = None,
        debug: bool = False,
        telemetry_config: Optional[Any] = None,
        telemetry_payload_store: Optional[Any] = None,
    ):
        """
        Initialize the OmniCoreAgent with user-friendly configuration.

        Args:
            name: Name of the agent
            system_instruction: System instruction for the agent
            model_config: Model configuration (dict or ModelConfig)
            mcp_tools: List of MCP tool configurations (optional)
            local_tools: LocalToolsIntegration instance (optional)
            sub_agents: SubAgentsIntegration instance (optional)
            agent_config: Optional agent configuration
            embedding_config: Optional embedding configuration
            memory_router: Optional memory router (MemoryRouter)
            telemetry_store: Optional telemetry store
            telemetry_recorder: Optional telemetry recorder
            telemetry_stream: Optional telemetry stream
            telemetry_exporters: Optional telemetry exporters
            telemetry_config: Optional TelemetryConfig or dictionary controlling
                built-in recording, redaction, and payload policy
            telemetry_payload_store: Optional built-in store for oversized
                redacted telemetry payloads
            debug: Enable debug logging
        """
        self.name = name
        self.system_instruction = system_instruction
        self.model_config = normalization.build_model_config(model_config)
        self.mcp_tools = normalization.build_mcp_tools(mcp_tools)
        self.local_tools = normalization.normalize_local_tools(local_tools)

        self.sub_agents = sub_agents
        self.agent_config = normalization.build_agent_config(name, agent_config)
        self.privacy_filter = PrivacyFilter.from_value(
            self.agent_config.get("privacy_config")
        )

        self.debug = debug
        self._cumulative_usage = None

        self.memory_router = memory_router
        self.telemetry_store = telemetry_store
        self.telemetry_recorder = telemetry_recorder
        self.telemetry_stream = telemetry_stream
        self._telemetry_retention_started = False
        self._telemetry_retention_last: dict[str, Any] | None = None
        self._telemetry_retention_automatic_runs = 0
        # A caller-chosen store is never silently replaced by a background
        # manager's store; a derived default may be.
        self._telemetry_store_explicit = any(
            component is not None
            for component in (telemetry_store, telemetry_recorder, telemetry_stream)
        )
        self.telemetry_exporters = self._build_telemetry_exporters(telemetry_exporters)
        self.telemetry_config = TelemetryConfig.from_value(telemetry_config)
        self.telemetry_payload_store = telemetry_payload_store
        if prompt_builder:
            self.prompt_builder = prompt_builder
        else:
            self.prompt_builder = LazyDefaultPromptBuilder()
        self.agent = None
        self.mcp_client = None
        self.llm_connection = None
        self.guardrail = None
        self._subagent_factory = None
        self.guardrail_mode = "full"  # Default: full protection

        self._initialized = False

    async def initialize(self):
        """Initialize the agent resources (memory, config, tools)"""
        if self._initialized:
            return

        if not self.memory_router:
            self.memory_router = construction.default_memory_router()

        self._ensure_telemetry()

        agent_cfg = self.agent_config
        self.guardrail_mode, self.guardrail = construction.build_guardrail(
            self.name, agent_cfg
        )

        self._create_agent()
        self._initialized = True
        for warning in self._security_warnings():
            runtime_logger().warning(f"{self.name}: {warning['message']}")

    def _create_agent(self):
        """Build and attach runtime components."""
        components = builder.build_agent_runtime(
            model_config=self.model_config,
            mcp_tools=self.mcp_tools,
            local_tools=self.local_tools,
            agent_config=self.agent_config,
            memory_router=self.memory_router,
            prompt_builder=self.prompt_builder,
            existing_subagent_factory=self._subagent_factory,
            guardrail=self.guardrail,
            guardrail_mode=self.guardrail_mode,
            summarize_fn=self._summarize_history,
            telemetry_recorder=self.telemetry_recorder,
            debug=self.debug,
        )

        self.agent = components.agent
        self.mcp_client = components.mcp_client
        self.llm_connection = components.llm_connection
        self.local_tools = components.local_tools
        self._subagent_factory = components.subagent_factory

    async def _summarize_history(
        self, messages: list[Dict[str, Any]], max_tokens: int = None
    ) -> str:
        """
        Callback for memory router to summarize message history using the agent's LLM.

        Args:
            messages: List of messages to summarize
            max_tokens: Optional token budget hint

        Returns:
            String summary of the messages
        """
        if not self.llm_connection:
            runtime_logger().warning("No LLM connection available for summarization")
            return ""

        instruction = summaries.summary_instruction(max_tokens)
        history_text = summaries.render_history(messages)

        prompt_messages = [
            {
                "role": "system",
                "content": instruction,
            },
            {
                "role": "user",
                "content": f"Here is the conversation history to summarize:\n\n{history_text}",
            },
        ]

        try:
            response = await self.llm_connection.llm_call(messages=prompt_messages)
            return summaries.extract_summary_text(response)
        except Exception as e:
            runtime_logger().error(f"Summarization callback failed: {e}")
            return ""

    def generate_session_id(self) -> str:
        """Generate a new session ID for the session"""
        return f"omni_core_agent_{self.name}_{uuid.uuid4().hex[:8]}"

    def generate_run_id(self) -> str:
        """Generate a unique run ID inside a session."""
        return f"run_{uuid.uuid4().hex}"

    def _ensure_telemetry(self) -> None:
        """Attach default telemetry components."""
        if self.telemetry_recorder is not None and self.telemetry_config is None:
            self.telemetry_config = getattr(self.telemetry_recorder, "config", None)

        if self.telemetry_payload_store is None and self.telemetry_recorder is not None:
            self.telemetry_payload_store = getattr(
                self.telemetry_recorder,
                "payload_store",
                None,
            )

        if self.telemetry_store is None:
            if self.telemetry_recorder is not None:
                self.telemetry_store = self.telemetry_recorder.store
            elif self.telemetry_stream is not None:
                self.telemetry_store = self.telemetry_stream.store
            else:
                self.telemetry_store = construction.default_telemetry_store(
                    telemetry_config=self.telemetry_config,
                    workspace_config=self.agent_config.get("workspace_config"),
                )

        if self.telemetry_payload_store is None:
            self.telemetry_payload_store = construction.default_telemetry_payload_store(
                telemetry_config=self.telemetry_config,
                workspace_config=self.agent_config.get("workspace_config"),
            )

        if self.telemetry_recorder is None:
            self.telemetry_recorder = TelemetryRecorder(
                self.telemetry_store,
                config=self.telemetry_config,
                exporters=self.telemetry_exporters,
                payload_store=self.telemetry_payload_store,
                privacy_filter=self.privacy_filter,
            )
        else:
            # The facade policy is the privacy boundary for all traces emitted
            # by this run, including an injected recorder or delegated child.
            self.telemetry_recorder.privacy_filter = self.privacy_filter
            if self.telemetry_recorder.store is not self.telemetry_store:
                raise ValueError(
                    "telemetry_recorder.store must be the same object as telemetry_store"
                )
            if self.telemetry_config is not None:
                recorder_config = getattr(self.telemetry_recorder, "config", None)
                if recorder_config != self.telemetry_config:
                    raise ValueError(
                        "telemetry_config must match telemetry_recorder.config when "
                        "both are provided"
                    )
            existing_exporters = getattr(self.telemetry_recorder, "exporters", None)
            if existing_exporters is None:
                raise ValueError(
                    "telemetry_recorder must expose exporters when telemetry_exporters "
                    "are provided"
                )
            for exporter in existing_exporters:
                if exporter not in self.telemetry_exporters:
                    self.telemetry_exporters.append(exporter)
            for exporter in self.telemetry_exporters:
                if exporter not in existing_exporters:
                    existing_exporters.append(exporter)
            recorder_payload_store = getattr(
                self.telemetry_recorder,
                "payload_store",
                None,
            )
            if (
                recorder_payload_store is not None
                and self.telemetry_payload_store is not None
                and recorder_payload_store is not self.telemetry_payload_store
            ):
                raise ValueError(
                    "telemetry_payload_store must match telemetry_recorder.payload_store"
                )
            if recorder_payload_store is None and self.telemetry_payload_store is not None:
                self.telemetry_recorder.payload_store = self.telemetry_payload_store

        if self.telemetry_config is None:
            self.telemetry_config = getattr(
                self.telemetry_recorder, "config", TelemetryConfig()
            )

        if self.telemetry_stream is None:
            self.telemetry_stream = TelemetryStream(self.telemetry_store)
        elif self.telemetry_stream.store is not self.telemetry_store:
            raise ValueError(
                "telemetry_stream.store must be the same object as telemetry_store"
            )

    def _inherit_telemetry(self, recorder: Any) -> None:
        """Use a parent recorder for a delegated child execution."""
        store = getattr(recorder, "store", None)
        if store is None:
            raise ValueError("A parent telemetry recorder must expose a store")
        self.telemetry_store = store
        self.telemetry_recorder = recorder
        self.telemetry_stream = TelemetryStream(store)
        self.telemetry_config = getattr(recorder, "config", None)
        self.telemetry_payload_store = getattr(recorder, "payload_store", None)
        self.privacy_filter = getattr(recorder, "privacy_filter", self.privacy_filter)
        self._bind_telemetry_components()

    def _adopt_telemetry_store(self, store: Any) -> None:
        """Record into a shared store while keeping this agent's policy.

        Used when a background manager owns telemetry for the agents it runs,
        so lifecycle and attempt traces live in one store. The agent's
        recording policy (config, exporters, payload store, privacy filter)
        is preserved; only the destination changes.
        """
        if self.telemetry_store is store:
            return
        if self._telemetry_store_explicit:
            raise ValueError(
                f"Agent {self.name!r} was given its own telemetry store; pass the "
                "same telemetry store to the agent and the background manager"
            )
        recorder = self.telemetry_recorder
        config = self.telemetry_config or getattr(recorder, "config", None)
        exporters = list(getattr(recorder, "exporters", None) or self.telemetry_exporters)
        payload_store = self.telemetry_payload_store or getattr(
            recorder, "payload_store", None
        )
        self.telemetry_store = store
        self.telemetry_stream = TelemetryStream(store)
        self.telemetry_config = config
        self.telemetry_exporters = exporters
        self.telemetry_payload_store = payload_store
        self.telemetry_recorder = TelemetryRecorder(
            store,
            config=config,
            exporters=exporters,
            payload_store=payload_store,
            privacy_filter=self.privacy_filter,
        )
        self._bind_telemetry_components()

    def _bind_telemetry_components(self) -> None:
        """Point components that captured a recorder at build time to the current one."""
        recorder = self.telemetry_recorder
        engine = getattr(self.agent, "governance_engine", None)
        if engine is not None:
            engine.telemetry_recorder = recorder
            sandbox = getattr(engine, "sandbox_runtime", None)
            if sandbox is not None and hasattr(sandbox, "telemetry_recorder"):
                sandbox.telemetry_recorder = recorder
        if self._subagent_factory is not None and hasattr(
            self._subagent_factory, "telemetry_recorder"
        ):
            self._subagent_factory.telemetry_recorder = recorder

    def _telemetry_actor(self) -> TelemetryActor:
        return TelemetryActor(type=ActorType.AGENT, name=self.name)

    def _telemetry_metadata(self) -> dict[str, Any]:
        metadata = {
            "agent_name": self.name,
            "model_provider": self.model_config.get("provider"),
            "model": self.model_config.get("model"),
            "guardrail_mode": self.agent_config.get("guardrail_mode", "full"),
            "privacy_config_version": self.privacy_filter.config.fingerprint(),
        }
        guardrail_mode = metadata["guardrail_mode"]
        if guardrail_mode != "off":
            from omnicoreagent.core.guardrails.models import DetectionConfig

            guardrail_config = DetectionConfig(
                **(self.agent_config.get("guardrail_config") or {})
            )
            metadata["guardrail_config_version"] = guardrail_config.fingerprint()
        fingerprint = getattr(self.telemetry_config, "fingerprint", None)
        if callable(fingerprint):
            metadata["telemetry_config_version"] = fingerprint()
        if self.telemetry_store is not None:
            storage_name = self.telemetry_store.__class__.__name__
            metadata["telemetry_storage"] = {
                "InMemoryTelemetryStore": "memory",
                "JsonlTelemetryStore": "jsonl",
            }.get(storage_name, storage_name)
        if self.telemetry_payload_store is not None:
            payload_storage_name = self.telemetry_payload_store.__class__.__name__
            metadata["telemetry_payload_storage"] = payload_storage_name
        return metadata

    def _telemetry_run_header(self) -> dict[str, Any]:
        """Describe the harness this run executes with, without secrets.

        The inner loop adds the model-facing tool catalog and system prompt
        and records the result as the run's ``run_configuration`` event.
        """
        config = self.agent_config
        metadata = self._telemetry_metadata()
        engine = getattr(self.agent, "governance_engine", None)
        policy = getattr(engine, "policy", None)
        return {
            "agent": {"name": self.name, "version": config.get("agent_version")},
            "model": {
                "provider": self.model_config.get("provider"),
                "model": self.model_config.get("model"),
                "settings": _model_settings(self.model_config),
            },
            "limits": {
                "max_steps": config.get("max_steps"),
                "tool_call_timeout": config.get("tool_call_timeout"),
                "request_limit": config.get("request_limit"),
                "total_tokens_limit": config.get("total_tokens_limit"),
            },
            "context_management": dict(config.get("context_management") or {}),
            "memory": dict(config.get("memory_config") or {}),
            "tool_offload": dict(config.get("tool_offload") or {}),
            "features": {
                "subagents": bool(config.get("enable_subagents")),
                "advanced_tool_use": bool(config.get("enable_advanced_tool_use")),
                "workspace_files": bool(config.get("enable_workspace_files")),
                "agent_skills": bool(config.get("enable_agent_skills")),
                "mcp_servers": len(self.mcp_tools or []),
            },
            "mcp_servers": self._mcp_server_status(),
            "guardrail": {"mode": metadata.get("guardrail_mode")},
            "security_warnings": self._security_warnings(),
            "governance": {
                "enabled": engine is not None,
                "policy_hash": getattr(
                    getattr(policy, "provenance", None), "policy_hash", None
                ),
            },
            "fingerprints": {
                "privacy": metadata.get("privacy_config_version"),
                "telemetry": metadata.get("telemetry_config_version"),
                "guardrail": metadata.get("guardrail_config_version"),
            },
        }

    def _security_warnings(self) -> list[dict[str, str]]:
        """Configurations where code runs with less protection than assumed."""
        config = self.agent_config
        governance = config.get("governance_config") or {}
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        skill_manager = getattr(getattr(self, "agent", None), "skill_manager", None)
        skills = bool(config.get("enable_agent_skills")) and bool(
            getattr(skill_manager, "skills", None)
        )
        warnings = []
        if engine is None:
            if skills:
                warnings.append(
                    {
                        "code": "ungoverned_host_scripts",
                        "message": "Governance is off: skill scripts run on the host "
                        "with no policy and no sandbox.",
                    }
                )
            if governance.get("sandbox_config") or governance.get("sandbox_runtime"):
                warnings.append(
                    {
                        "code": "sandbox_unused_without_governance",
                        "message": "A sandbox is configured but governance is off, so "
                        "nothing runs in it; enable governance to use it.",
                    }
                )
        elif skills and not self.can_execute:
            warnings.append(
                {
                    "code": "host_scripts_not_contained",
                    "message": "Skill scripts run on the host: governed by policy but "
                    "not contained by a sandbox.",
                }
            )
        return warnings

    @property
    def can_execute(self) -> bool:
        """Whether this agent has a sandbox that can run commands.

        Uses the same check governance uses before allowing a sandboxed
        action, so a tool is never offered that governance would refuse.
        """
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        runtime = getattr(engine, "sandbox_runtime", None)
        return bool(
            engine is not None
            and getattr(runtime, "supports_execution", False)
            and engine._sandbox_runtime_satisfies_required_boundary()
        )

    @property
    def sandbox_execution(self):
        """The governed route from this agent to its sandbox, or None."""
        return getattr(getattr(self, "agent", None), "sandbox_execution", None)

    def _mcp_server_status(self) -> list[dict[str, Any]]:
        """Configured MCP servers with their state at the start of the run."""
        if self.mcp_client is not None:
            return self.mcp_client.server_status()
        return [
            {
                "name": server.get("name"),
                "transport_type": server.get("transport_type", "stdio"),
                "status": "not_connected",
            }
            for server in self.mcp_tools or []
        ]

    def _telemetry_scope(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ) -> TelemetryStreamScope:
        return TelemetryStreamScope(
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            event_types=event_types,
        )

    async def connect_mcp_servers(self):
        """Connect to MCP servers if MCP tools are configured"""
        if not self._initialized:
            await self.initialize()

        if self.mcp_client and self.mcp_tools:
            await self.mcp_client.connect_to_servers()

    async def run(
        self,
        query: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        on_event: Any = None,
        *,
        tags: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run the agent with a query and optional session ID.

        Args:
            query: The user query
            session_id: Optional session ID for session continuity
            run_id: Optional run ID for serving/telemetry correlation
            tags: Optional labels recorded on the run's trace
            provenance: Optional external identity for the trace, such as
                ``evaluation_id``, ``case_id``, ``trial_id``,
                ``environment_id``, or ``external_ids``; recorded without
                changing execution

        Returns:
            Dict containing response, session_id, trace_id, and run_id.
        """
        if not session_id:
            session_id = self.generate_session_id()

        self._ensure_telemetry()
        if not self._telemetry_retention_started:
            # The configured retention window applies once per agent, before
            # its first trace, so disk use stays bounded without a manual call.
            self._telemetry_retention_started = True
            await self._apply_telemetry_retention(trigger="automatic")

        run_id = run_id or self.generate_run_id()
        trace_context = None
        run_tracker = None
        # Set once this run starts finalizing its own trace. A telemetry
        # failure after that point has already restored the parent context,
        # so the error handlers below must not record anything more.
        trace_finalizing = False
        delivery = (
            streaming.StreamDelivery(
                on_event,
                run_id,
                privacy_filter=self.privacy_filter,
            )
            if on_event is not None
            else streaming.current_delivery.get()
        )
        delivery_token = streaming.current_delivery.set(delivery)
        try:
            trace_context = await self.telemetry_recorder.start_trace(
                name="agent.run",
                kind="agent.run",
                actor=self._telemetry_actor(),
                run_id=run_id,
                session_id=session_id,
                agent_id=self.name,
                provenance=provenance,
                metadata={**self._telemetry_metadata(), "tags": list(tags or [])},
                input={"query": query},
            )
            await self.telemetry_recorder.emit_event(
                "user_message",
                actor=TelemetryActor(type=ActorType.USER),
                input={"message": query},
            )

            if not self._initialized:
                await self.initialize()

            # The run's durable record lives in the chosen memory store.
            run_tracker = RunTracker(
                self.memory_router,
                run_id=run_id,
                session_id=session_id,
                agent_name=self.name,
                agent_version=self.agent_config.get("agent_version"),
            )
            await run_tracker.start(trace_context.trace_id)

            blocked_response = await execution.blocked_guardrail_response(
                guardrail=self.guardrail,
                query=query,
                session_id=session_id,
                agent_name=self.name,
                telemetry_recorder=self.telemetry_recorder,
            )
            if blocked_response:
                await self.telemetry_recorder.emit_event(
                    "guardrail_violation",
                    actor=TelemetryActor(type=ActorType.GUARDRAIL, name=self.name),
                    input={"query": query},
                    output=blocked_response.get("guardrail_result"),
                )
                run_summary = await self._run_summary(trace_context.trace_id)
                await self.telemetry_recorder.emit_event(
                    "final_answer",
                    actor=self._telemetry_actor(),
                    output={"response": blocked_response["response"]},
                    metadata=run_summary,
                )
                trace_finalizing = True
                await self.telemetry_recorder.end_trace(
                    status=TraceStatus.ABORTED_SAFETY_GUARD,
                    output={
                        "response": blocked_response["response"],
                        "run_summary": run_summary["run_summary"],
                    },
                )
                await run_tracker.finish("blocked")
                blocked_response["trace_id"] = trace_context.trace_id
                blocked_response["run_id"] = run_id
                return self.privacy_filter.redact(
                    blocked_response, boundary="public"
                )

            runtime_prompt = self.prompt_builder.build(
                system_instruction=self.system_instruction
            )

            async def emit_delta(event):
                await delivery.emit(
                    event,
                    agent_name=self.name,
                    run_id=run_id,
                    session_id=session_id,
                    trace_id=trace_context.trace_id,
                )

            async with run_tracker.active():
                response = await self.agent.run(
                    **({"on_event": emit_delta} if delivery is not None else {}),
                    system_prompt=runtime_prompt,
                    query=query,
                    llm_connection=self.llm_connection,
                    add_message_to_history=self._store_message_with_telemetry,
                    message_history=self._get_messages_with_telemetry,
                    debug=self.debug,
                    telemetry_recorder=self.telemetry_recorder,
                    telemetry_run_header=self._telemetry_run_header(),
                    **execution.build_agent_run_kwargs(
                        mcp_client=self.mcp_client,
                        local_tools=self.local_tools,
                        session_id=session_id,
                        sub_agents=self.sub_agents,
                    ),
                )

            formatted_response = execution.format_run_response(
                response=response,
                session_id=session_id,
                agent_name=self.name,
                usage_getter=self._usage,
            )
            trace_status = TraceStatus(
                formatted_response.pop(
                    "_trace_status",
                    TraceStatus.COMPLETED.value
                    if formatted_response.get("status", "success") == "success"
                    else TraceStatus.FAILED.value,
                )
            )
            await run_tracker.finish(
                "completed" if trace_status == TraceStatus.COMPLETED else "failed",
                usage=formatted_response.get("metric"),
            )
            run_summary = await self._run_summary(trace_context.trace_id)
            await self.telemetry_recorder.emit_event(
                "final_answer",
                actor=self._telemetry_actor(),
                output={
                    "response": formatted_response.get("response"),
                    "status": formatted_response.get("status", "success"),
                    "termination_reason": formatted_response.get("termination_reason"),
                },
                metadata=run_summary,
            )
            trace_finalizing = True
            await self.telemetry_recorder.end_trace(
                status=trace_status,
                output={
                    "response": formatted_response.get("response"),
                    "status": formatted_response.get("status", "success"),
                    "termination_reason": formatted_response.get("termination_reason"),
                    "run_summary": run_summary["run_summary"],
                },
            )
            formatted_response["trace_id"] = trace_context.trace_id
            formatted_response["run_id"] = run_id
            return self.privacy_filter.redact(formatted_response, boundary="public")
        except asyncio.CancelledError as exc:
            await self._finish_run_record(run_tracker, "cancelled", exc)
            if trace_context is not None and not trace_finalizing:
                stopped_status = (
                    TraceStatus.TIMEOUT
                    if current_stop_reason() == "timeout"
                    else TraceStatus.CANCELLED
                )
                try:
                    # Cleanup must finish even if the caller keeps cancelling.
                    await complete_despite_cancellation(
                        self._end_trace_after_failure(
                            trace_context, exc, status=stopped_status
                        )
                    )
                except Exception as telemetry_exc:
                    # Cancellation must keep propagating; a strict telemetry
                    # failure cannot replace it.
                    runtime_logger().warning(
                        f"Telemetry finalization failed during cancellation: "
                        f"{telemetry_exc.__class__.__name__}"
                    )
            raise
        except Exception as exc:
            await self._finish_run_record(run_tracker, "failed", exc)
            if trace_context is not None and not trace_finalizing:
                await self._end_trace_after_failure(
                    trace_context, exc, status=TraceStatus.FAILED
                )
            raise

        finally:
            streaming.current_delivery.reset(delivery_token)

    async def prune_telemetry(self) -> Dict[str, Any]:
        """Apply the configured trace and payload retention now.

        Expired finished traces are removed first; payloads are then pruned
        by their own retention window, except any payload still referenced by
        a kept trace. Returns what was removed.
        """
        self._ensure_telemetry()
        return await self._apply_telemetry_retention(trigger="explicit")

    def telemetry_retention_status(self) -> Dict[str, Any]:
        """Report the retention policy and the most recent cleanup results."""
        self._ensure_telemetry()
        trace_status = getattr(self.telemetry_store, "retention_status", None)
        payload_status = getattr(self.telemetry_payload_store, "retention_status", None)
        return {
            "trace_store": trace_status() if callable(trace_status) else None,
            "payload_store": payload_status() if callable(payload_status) else None,
            "last_cleanup": self._telemetry_retention_last,
            "automatic_runs": self._telemetry_retention_automatic_runs,
        }

    async def _apply_telemetry_retention(self, *, trigger: str) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "trigger": trigger,
            "traces_removed": 0,
            "payloads_removed": 0,
            "payloads_retained_by_reference": 0,
            "error": None,
        }
        try:
            prune_traces = getattr(self.telemetry_store, "prune", None)
            if callable(prune_traces):
                summary["traces_removed"] = await prune_traces(trigger=trigger)
            payload_store = self.telemetry_payload_store
            prune_payloads = getattr(payload_store, "prune", None)
            if callable(prune_payloads):
                references = await self._stored_payload_references()
                summary["payloads_removed"] = await asyncio.to_thread(
                    prune_payloads, references=references
                )
                summary["payloads_retained_by_reference"] = (
                    getattr(payload_store, "last_prune", None) or {}
                ).get("retained_by_reference", len(references))
        except Exception as exc:
            # Retention is housekeeping; it never fails a run. The failure
            # stays visible in the retention status.
            summary["error"] = f"{exc.__class__.__name__}: {exc}"
            runtime_logger().warning(f"Telemetry retention failed: {summary['error']}")
        summary["at"] = datetime.now(timezone.utc).isoformat()
        self._telemetry_retention_last = summary
        if trigger == "automatic":
            self._telemetry_retention_automatic_runs += 1
        if summary["traces_removed"] or summary["payloads_removed"]:
            runtime_logger().info(
                f"Telemetry retention ({trigger}) removed "
                f"{summary['traces_removed']} trace(s) and "
                f"{summary['payloads_removed']} payload(s)"
            )
        return summary

    async def _finish_run_record(
        self, run_tracker: Any, status: str, exc: BaseException
    ) -> None:
        """Record how a run ended; it must not replace the original error."""
        if run_tracker is None or run_tracker.record["status"] != "running":
            return
        try:
            await complete_despite_cancellation(run_tracker.finish(status, error=exc))
        except Exception as record_exc:
            runtime_logger().warning(
                f"Could not record run {run_tracker.run_id} as {status}: "
                f"{record_exc.__class__.__name__}"
            )

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """A run's durable record, or None if it has none.

        The record is kept in the agent's memory store: status, step, usage,
        trace IDs, and each tool call's state (arguments as a digest).
        """
        if not self._initialized:
            await self.initialize()
        if not supports_run_state(self.memory_router):
            return None
        try:
            return await self.memory_router.get_run_state(run_id)
        except RunStateUnsupported:
            return None

    async def resolve_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        decision: str,
        approver: str,
        note: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Decide an approval a run is waiting for.

        ``decision`` is ``"approve"`` or ``"deny"``. A denial's ``note``
        reaches the model. ``arguments`` approves an edited call instead of
        the one asked for. The decision applies once, to that exact request.
        """
        from omnicoreagent.core.run_approvals import decide

        if not self._initialized:
            await self.initialize()
        if not supports_run_state(self.memory_router):
            raise LookupError(f"No run {run_id}: the memory store keeps no run state")
        return await decide(
            self.memory_router,
            run_id,
            approval_id,
            decision=decision,
            approver=approver,
            note=note,
            arguments=arguments,
        )

    async def list_runs(
        self,
        session_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Run records, oldest first, optionally for one session or status."""
        if not self._initialized:
            await self.initialize()
        if not supports_run_state(self.memory_router):
            return []
        try:
            return await self.memory_router.list_run_states(session_id, status, limit)
        except RunStateUnsupported:
            return []

    async def _end_trace_after_failure(
        self,
        trace_context: TelemetryContext,
        exc: BaseException,
        *,
        status: TraceStatus,
    ) -> None:
        """Record a run failure on this run's own trace and end it.

        The active context can differ from ``trace_context`` (for example a
        nested span left open by the failure), but it must never be a parent
        trace: failures are always attributed to the run's own trace.
        """

        recorder = self.telemetry_recorder
        current = recorder.current_context()
        if current is None or current.trace_id != trace_context.trace_id:
            set_telemetry_context(trace_context)
        error = (
            {"type": "TimeoutError", "message": "Run exceeded its deadline"}
            if status == TraceStatus.TIMEOUT
            else {"type": exc.__class__.__name__, "message": str(exc)}
        )
        # A failed, cancelled, or timed-out run still consumed steps, tokens,
        # and cost; its totals are recorded with its terminal event.
        run_summary = await self._run_summary(trace_context.trace_id)
        try:
            if status in {TraceStatus.CANCELLED, TraceStatus.TIMEOUT}:
                await recorder.emit_event(
                    "final_state",
                    actor=self._telemetry_actor(),
                    output={"status": status.value},
                    metadata=run_summary,
                )
            else:
                await recorder.record_exception(
                    exc,
                    event_type="runtime_error",
                    actor=self._telemetry_actor(),
                    metadata={"phase": "agent.run", **run_summary},
                )
        finally:
            await recorder.end_trace(
                status=status,
                error=error,
                output={"run_summary": run_summary["run_summary"]},
            )

    async def _run_summary(self, trace_id: str) -> Dict[str, Any]:
        """Totals for the run so far, with its subagents' tokens and cost added."""
        recorder = self.telemetry_recorder
        trace = await recorder.read_trace(trace_id)
        if trace is None:
            return {"run_summary": None, "final_model_response_event_id": None}
        summary = summarize_trace(trace)
        combined_tokens = dict(summary["tokens"])
        combined_cost = summary["estimated_cost_usd"] or 0.0
        cost_complete = summary["cost_complete"] or summary["model_calls"]["total"] == 0
        for child_id in summary["subagents"]["child_trace_ids"]:
            child = await recorder.read_trace(child_id)
            if child is None:
                cost_complete = False
                continue
            child_summary = summarize_trace(child)
            for key, value in child_summary["tokens"].items():
                combined_tokens[key] = combined_tokens.get(key, 0) + value
            combined_cost += child_summary["estimated_cost_usd"] or 0.0
            if child_summary["model_calls"]["total"] and not child_summary["cost_complete"]:
                cost_complete = False
        summary["including_subagents"] = {
            "tokens": combined_tokens,
            "estimated_cost_usd": round(combined_cost, 10),
            "cost_complete": cost_complete,
        }
        return {
            "run_summary": summary,
            "final_model_response_event_id": final_model_response_event_id(trace),
        }

    def stream(
        self, query: str, session_id: str | None = None, run_id: str | None = None
    ):
        """Stream live intermediate text and one terminal result from the same run loop.

        Use ``contextlib.aclosing`` when stopping iteration early. Deltas are not
        replayed from telemetry; the completed answer is persisted normally.
        """
        return streaming.stream_run(self, query, session_id=session_id, run_id=run_id)

    async def _store_message_with_telemetry(
        self,
        role: str,
        content: str,
        metadata: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        run = current_run()
        if run is not None:
            # Every message says which request produced it.
            metadata = {**(metadata or {}), "run_id": run.run_id}
        stored_content = self.privacy_filter.redact(content, boundary="memory")
        stored_metadata = _without_changed_continuation(
            metadata, self.privacy_filter.redact(metadata, boundary="memory")
        )
        stored_message_digest = stable_message_digest(
            {
                "role": role,
                "content": stored_content,
                "metadata": stored_metadata,
            }
        )
        if self.telemetry_recorder is None:
            await self.memory_router.store_message(
                role, stored_content, stored_metadata, session_id
            )
            if run is not None:
                await run.add_message(
                    {"role": role, "content": stored_content, "metadata": stored_metadata}
                )
            return
        span = await self.telemetry_recorder.start_span(
            name="memory.write",
            kind="memory.write",
            actor=TelemetryActor(type=ActorType.MEMORY),
            input={
                "role": role,
                "session_id": session_id,
                "metadata_keys": sorted((metadata or {}).keys()),
            },
        )
        try:
            await self.memory_router.store_message(
                role, stored_content, stored_metadata, session_id
            )
            if run is not None:
                await run.add_message(
                    {"role": role, "content": stored_content, "metadata": stored_metadata}
                )
            await self.telemetry_recorder.emit_event(
                "memory_write",
                actor=TelemetryActor(type=ActorType.MEMORY),
                input={"role": role, "session_id": session_id},
                output={"stored": True, "message_digest": stored_message_digest},
            )
            await self.telemetry_recorder.end_span(
                span.span_id,
                status="ok",
                output={"stored": True, "message_digest": stored_message_digest},
            )
        except Exception as exc:
            await self.telemetry_recorder.emit_event(
                "memory_write",
                actor=TelemetryActor(type=ActorType.MEMORY),
                input={"role": role, "session_id": session_id},
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            await self.telemetry_recorder.end_span(
                span.span_id,
                status="error",
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    async def _get_messages_with_telemetry(
        self,
        session_id: str,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.telemetry_recorder is None:
            messages = await self.memory_router.get_messages(session_id, agent_name)
            await _keep_run_history(messages)
            return messages
        span = await self.telemetry_recorder.start_span(
            name="memory.read",
            kind="memory.read",
            actor=TelemetryActor(type=ActorType.MEMORY),
            input={"session_id": session_id, "agent_name": agent_name},
        )
        try:
            messages = await self.memory_router.get_messages(session_id, agent_name)
            await _keep_run_history(messages)
            message_digests = [stable_message_digest(message) for message in messages]
            await self.telemetry_recorder.emit_event(
                "memory_read",
                actor=TelemetryActor(type=ActorType.MEMORY),
                input={"session_id": session_id, "agent_name": agent_name},
                output={
                    "message_count": len(messages),
                    "message_digests": message_digests,
                },
            )
            await self.telemetry_recorder.end_span(
                span.span_id,
                status="ok",
                output={
                    "message_count": len(messages),
                    "message_digests": message_digests,
                },
            )
            return messages
        except Exception as exc:
            await self.telemetry_recorder.emit_event(
                "memory_read",
                actor=TelemetryActor(type=ActorType.MEMORY),
                input={"session_id": session_id, "agent_name": agent_name},
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            await self.telemetry_recorder.end_span(
                span.span_id,
                status="error",
                error={"type": exc.__class__.__name__, "message": str(exc)},
            )
            raise

    async def get_metrics(self) -> Dict[str, Any]:
        """
        Get the cumulative metrics for the lifecycle of the agent.

        Returns:
            Dict containing total requests, tokens, and time.
        """
        cumulative_usage = self._usage()
        average_time = (
            cumulative_usage.total_time / cumulative_usage.requests
            if cumulative_usage.requests > 0
            else 0
        )
        return {
            "total_requests": cumulative_usage.requests,
            "total_request_tokens": cumulative_usage.request_tokens,
            "total_response_tokens": cumulative_usage.response_tokens,
            "total_tokens": cumulative_usage.total_tokens,
            "total_time": cumulative_usage.total_time,
            "average_time": average_time,
        }

    def _usage(self):
        if self._cumulative_usage is None:
            self._cumulative_usage = runtime("Usage")()
        return self._cumulative_usage

    def _build_telemetry_exporters(
        self,
        exporters: Optional[List[Any]],
    ) -> list[TelemetryExporter]:
        built: list[TelemetryExporter] = []
        for exporter in exporters or []:
            if isinstance(exporter, str):
                built.append(build_telemetry_exporter(exporter))
            elif isinstance(exporter, dict):
                config = dict(exporter)
                destination = (
                    config.pop("destination", None)
                    or config.pop("type", None)
                    or config.pop("name", None)
                )
                if destination is None:
                    raise ValueError(
                        "Telemetry exporter config requires destination, type, or name"
                    )
                built.append(build_telemetry_exporter(str(destination), **config))
            elif hasattr(exporter, "export_trace"):
                built.append(exporter)
            else:
                raise ValueError(
                    "Telemetry exporters must be exporter instances, names, or dicts"
                )
        return built

    async def list_all_available_tools(self):
        """List all available tools (MCP and local)"""
        if not self._initialized:
            await self.initialize()

        runtime_local_tools = self.local_tools
        if self.agent and hasattr(self.agent, "tool_runtime_registry"):
            runtime_local_tools = await self.agent.tool_runtime_registry.prepare_tools(
                local_tools=self.local_tools
            )

        return harness_tools.available_tools(self.mcp_client, runtime_local_tools)

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Get session history for a specific session ID"""
        if not self.memory_router:
            return []

        return await self.memory_router.get_messages(
            session_id=session_id, agent_name=self.name
        )

    async def clear_session_history(self, session_id: Optional[str] = None):
        """Clear session history for a specific session ID or all history"""
        if not self.memory_router:
            return

        if session_id:
            await self.memory_router.clear_memory(
                session_id=session_id, agent_name=self.name
            )
        else:
            await self.memory_router.clear_memory(agent_name=self.name)

    async def get_trace(
        self,
        identifier: str | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        normalize: bool = False,
    ) -> Dict[str, Any] | None:
        """
        Return telemetry trace data.
        """
        filters = [value is not None for value in (session_id, trace_id, run_id)]
        if identifier is not None and any(filters):
            raise ValueError("Use either identifier or trace lookup keyword arguments")
        if sum(filters) > 1:
            raise ValueError("Use only one of trace_id, run_id, or session_id")
        if trace_id is not None:
            return await self.get_telemetry_trace(trace_id, normalize=normalize)
        if run_id is not None:
            traces = await self.list_telemetry_traces(
                run_id=run_id, normalize=normalize
            )
            return traces[-1] if traces else None
        if session_id is not None:
            return await self.get_latest_trace(session_id, normalize=normalize)
        if identifier is None:
            raise TypeError("get_trace() requires trace_id or session_id")
        exact_trace = await self.get_telemetry_trace(identifier, normalize=normalize)
        if exact_trace is not None:
            return exact_trace
        return await self.get_latest_trace(identifier, normalize=normalize)

    async def get_latest_trace(
        self,
        session_id: str,
        *,
        normalize: bool = False,
    ) -> Dict[str, Any] | None:
        traces = await self.list_telemetry_traces(
            session_id=session_id,
            normalize=normalize,
        )
        return traces[-1] if traces else None

    async def get_telemetry_trace(
        self,
        trace_id: str,
        *,
        normalize: bool = False,
    ) -> Dict[str, Any] | None:
        self._ensure_telemetry()
        trace = await self.telemetry_store.get_trace(trace_id)
        if trace and normalize:
            trace = TelemetryNormalizer().normalize(trace)
        return trace.model_dump() if trace else None

    async def read_telemetry_payload(self, reference: str) -> Any:
        """Read a redacted oversized payload referenced by telemetry."""
        self._ensure_telemetry()
        payload_store = self.telemetry_payload_store
        if payload_store is None:
            raise ValueError("No telemetry payload store is configured")
        return await asyncio.to_thread(payload_store.read, reference)

    async def prune_telemetry_payloads(self, retention_days: int | None = None) -> int:
        """Prune payloads while retaining references in currently stored traces."""
        self._ensure_telemetry()
        payload_store = self.telemetry_payload_store
        if payload_store is None:
            return 0
        return await asyncio.to_thread(
            payload_store.prune,
            retention_days,
            references=await self._stored_payload_references(),
        )

    async def _stored_payload_references(self) -> set[str]:
        references: set[str] = set()
        for trace in await self.telemetry_store.list_traces():
            references |= payload_references(trace)
        return references

    async def export_trace(
        self,
        identifier: str | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        exporters: Optional[List[Any]] = None,
        normalize: bool = True,
        strict: bool = False,
    ) -> list[Dict[str, Any]]:
        """
        Export a telemetry trace through configured or supplied exporters.
        """
        trace = await self._resolve_telemetry_trace(
            identifier,
            session_id=session_id,
            trace_id=trace_id,
            run_id=run_id,
            normalize=normalize,
        )
        if trace is None:
            return []
        selected_exporters = (
            self._build_telemetry_exporters(exporters)
            if exporters is not None
            else self.telemetry_exporters
        )
        results = await export_trace_to_many(
            trace,
            selected_exporters,
            strict=strict,
        )
        return [result.model_dump() for result in results]

    async def _resolve_telemetry_trace(
        self,
        identifier: str | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        normalize: bool = False,
    ) -> TelemetryTrace | None:
        self._ensure_telemetry()
        filters = [value is not None for value in (session_id, trace_id, run_id)]
        if identifier is not None and any(filters):
            raise ValueError("Use either identifier or trace lookup keyword arguments")
        if sum(filters) > 1:
            raise ValueError("Use only one of trace_id, run_id, or session_id")
        if trace_id is not None:
            trace = await self.telemetry_store.get_trace(trace_id)
        elif run_id is not None:
            traces = await self.telemetry_store.list_traces(TraceFilter(run_id=run_id))
            trace = traces[-1] if traces else None
        elif session_id is not None:
            traces = await self.telemetry_store.list_traces(
                TraceFilter(session_id=session_id)
            )
            trace = traces[-1] if traces else None
        elif identifier is not None:
            trace = await self.telemetry_store.get_trace(identifier)
            if trace is None:
                traces = await self.telemetry_store.list_traces(
                    TraceFilter(session_id=identifier)
                )
                trace = traces[-1] if traces else None
        else:
            raise TypeError("export_trace() requires trace_id or session_id")
        if trace and normalize:
            trace = TelemetryNormalizer().normalize(trace)
        return trace

    async def list_telemetry_traces(
        self,
        trace_filter: TraceFilter | None = None,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        workflow_id: str | None = None,
        model: str | None = None,
        status: TraceStatus | str | None = None,
        normalize: bool = False,
    ) -> list[Dict[str, Any]]:
        self._ensure_telemetry()
        if trace_filter is not None and any(
            value is not None
            for value in (
                trace_id,
                run_id,
                session_id,
                task_id,
                agent_id,
                workflow_id,
                model,
                status,
            )
        ):
            raise ValueError(
                "Use either trace_filter or telemetry filter keyword arguments, not both"
            )
        if trace_filter is None:
            trace_filter = TraceFilter(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                task_id=task_id,
                agent_id=agent_id,
                workflow_id=workflow_id,
                model=model,
                status=status,
            )
        traces = await self.telemetry_store.list_traces(trace_filter)
        if normalize:
            normalizer = TelemetryNormalizer()
            traces = [normalizer.normalize(trace) for trace in traces]
        return [trace.model_dump() for trace in traces]

    async def get_trace_family(
        self,
        identifier: str | None = None,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        normalize: bool = False,
    ) -> list[Dict[str, Any]]:
        """Return all locally stored traces linked to one execution boundary.

        A family follows explicit parent links in both directions. For a
        ``run_id`` seed, every trace carrying that run id is included before
        linked ancestors and descendants are added. This avoids treating the
        latest trace for a session as the complete execution history.
        """
        selectors = [value is not None for value in (trace_id, run_id)]
        if identifier is not None and any(selectors):
            raise ValueError("Use either identifier or trace lookup keyword arguments")
        if sum(selectors) > 1:
            raise ValueError("Use only one of trace_id or run_id")
        if identifier is not None:
            trace_id = identifier
        if trace_id is None and run_id is None:
            raise TypeError("get_trace_family() requires trace_id or run_id")

        self._ensure_telemetry()
        traces = await self.telemetry_store.list_traces()
        by_id = {trace.trace_id: trace for trace in traces}
        children: dict[str, set[str]] = {}
        if trace_id is not None:
            seed_ids = {trace_id} if trace_id in by_id else set()
        else:
            seed_ids = {trace.trace_id for trace in traces if trace.run_id == run_id}

        for trace in traces:
            if trace.parent_trace_id:
                children.setdefault(trace.parent_trace_id, set()).add(trace.trace_id)

        family_ids: set[str] = set()
        pending = list(seed_ids)
        while pending:
            current = pending.pop()
            if current in family_ids:
                continue
            trace = by_id.get(current)
            if trace is None:
                continue
            family_ids.add(current)
            if trace.parent_trace_id:
                pending.append(trace.parent_trace_id)
            pending.extend(children.get(current, ()))

        selected = _lineage_order(
            [trace for trace in traces if trace.trace_id in family_ids]
        )
        if normalize:
            normalizer = TelemetryNormalizer()
            selected = [normalizer.normalize(trace) for trace in selected]
        return [trace.model_dump() for trace in selected]

    async def get_trajectory(
        self,
        identifier: str | None = None,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        include_children: bool = True,
        max_depth: int = 5,
    ) -> Dict[str, Any] | None:
        """Return one run as an ordered trajectory, from request to final answer.

        Look up by ``trace_id`` (exact) or ``run_id`` (the latest agent run with
        that ID; other traces for the run are listed in
        ``other_trace_ids_for_run``). Delegated child runs are nested under the
        tool call that started them. See ``build_trajectory`` for the shape.
        """
        if identifier is not None and (trace_id is not None or run_id is not None):
            raise ValueError("Use either identifier or trace_id/run_id")
        if trace_id is None and run_id is None:
            trace_id = identifier
        if trace_id is None and run_id is None:
            raise TypeError("get_trajectory() requires trace_id or run_id")
        self._ensure_telemetry()
        other_trace_ids: list[str] = []
        if trace_id is not None:
            trace = await self.telemetry_store.get_trace(trace_id)
        else:
            candidates = [
                candidate
                for candidate in await self.telemetry_store.list_traces(
                    TraceFilter(run_id=run_id)
                )
                if candidate.spans
                and any(
                    span.span_id == candidate.root_span_id and span.kind == "agent.run"
                    for span in candidate.spans
                )
            ]
            trace = candidates[-1] if candidates else None
            other_trace_ids = [c.trace_id for c in candidates[:-1]]
        if trace is None:
            return None
        trajectory = await self._trajectory_for(
            trace, include_children=include_children, depth=max_depth, seen=set()
        )
        if run_id is not None:
            trajectory["other_trace_ids_for_run"] = other_trace_ids
        return trajectory

    async def _trajectory_for(
        self,
        trace: TelemetryTrace,
        *,
        include_children: bool,
        depth: int,
        seen: set[str],
    ) -> Dict[str, Any]:
        seen.add(trace.trace_id)
        children: dict[str, Dict[str, Any]] = {}
        if include_children and depth > 0:
            for child_id in summarize_trace(trace)["subagents"]["child_trace_ids"]:
                if child_id in seen:
                    continue
                child = await self.telemetry_store.get_trace(child_id)
                if child is not None:
                    children[child_id] = await self._trajectory_for(
                        child,
                        include_children=include_children,
                        depth=depth - 1,
                        seen=seen,
                    )
        return build_trajectory(trace, children=children)

    async def get_telemetry_stream_cursor(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ) -> str | None:
        self._ensure_telemetry()
        return await self.telemetry_stream.get_stream_cursor(
            self._telemetry_scope(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                task_id=task_id,
                event_types=event_types,
            )
        )

    async def stream_telemetry_after(
        self,
        *,
        cursor: str | None,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ):
        self._ensure_telemetry()
        scope = self._telemetry_scope(
            trace_id=trace_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            event_types=event_types,
        )
        async for event in self.telemetry_stream.stream_after(scope, cursor):
            yield event

    async def get_telemetry_events_after(
        self,
        *,
        cursor: str | None,
        trace_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        event_types: tuple[str, ...] | None = None,
    ):
        self._ensure_telemetry()
        return await self.telemetry_stream.get_events_after(
            self._telemetry_scope(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                task_id=task_id,
                event_types=event_types,
            ),
            cursor,
        )

    async def get_memory_store_type(self) -> str:
        """Get the current memory store type."""
        return self.memory_router.memory_store_type

    async def switch_memory_store(self, memory_store_type: str):
        """Switch to a different memory store type."""
        self.memory_router.switch_memory_store(memory_store_type)

    async def cleanup(self):
        """Clean up resources"""
        if self._subagent_factory:
            await self._subagent_factory.cleanup()
            self._subagent_factory = None
        if self.mcp_client:
            await self.mcp_client.cleanup()

    async def cleanup_mcp_servers(self):
        """Clean up MCP servers without removing the agent and the config"""
        if self.mcp_client:
            await self.mcp_client.cleanup()


def _lineage_order(traces: list[TelemetryTrace]) -> list[TelemetryTrace]:
    """Order a trace family parent-first, depth-first, siblings by start time.

    A parent and child can start within the same clock tick, so start time
    alone cannot guarantee that a parent is listed before its children.
    """

    def start_key(trace: TelemetryTrace) -> tuple[Any, str]:
        return (trace.started_at, trace.trace_id)

    by_id = {trace.trace_id: trace for trace in traces}
    children: dict[str, list[TelemetryTrace]] = {}
    roots: list[TelemetryTrace] = []
    for trace in traces:
        if trace.parent_trace_id in by_id and trace.parent_trace_id != trace.trace_id:
            children.setdefault(trace.parent_trace_id, []).append(trace)
        else:
            roots.append(trace)
    ordered: list[TelemetryTrace] = []
    seen: set[str] = set()
    stack = sorted(roots, key=start_key, reverse=True)
    while stack:
        trace = stack.pop()
        if trace.trace_id in seen:
            continue
        seen.add(trace.trace_id)
        ordered.append(trace)
        stack.extend(
            sorted(children.get(trace.trace_id, []), key=start_key, reverse=True)
        )
    # A malformed parent cycle has no root; keep those traces in start order.
    ordered.extend(
        trace for trace in sorted(traces, key=start_key) if trace.trace_id not in seen
    )
    return ordered


_MODEL_CONFIG_IDENTITY_KEYS = frozenset({"provider", "model"})
_MODEL_CONFIG_PRIVATE_MARKERS = ("key", "secret", "token_", "endpoint", "host", "url")


def _model_settings(model_config: dict[str, Any]) -> dict[str, Any]:
    """Generation settings from a model config, excluding credentials and endpoints."""
    settings: dict[str, Any] = {}
    for key, value in model_config.items():
        name = str(key).lower()
        if name in _MODEL_CONFIG_IDENTITY_KEYS or value in (None, "N/A"):
            continue
        if any(marker in name for marker in _MODEL_CONFIG_PRIVATE_MARKERS):
            continue
        if name.startswith("azure_") or name.startswith("aws_"):
            continue
        settings[key] = value
    return settings


async def _keep_run_history(messages: list[dict[str, Any]]) -> None:
    """The history a run loads first is the history it resumes from."""
    run = current_run()
    if run is not None:
        await run.set_history(messages)
