from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from omnicoreagent.core.budgets import (
    BudgetLedger,
    RunAwaitingBudget,
    RunBudgets,
    active_budgets,
)
from omnicoreagent.core.runs import (
    ClaimHeartbeat,
    RunInterrupted,
    RunStateConflict,
    RunStateUnsupported,
    RunSuspended,
    SIDE_EFFECT_STATUSES,
    RunTracker,
    current_run,
    decided_waiting,
    lease_expired,
    note_side_effects,
    resume_cause,
    supports_run_state,
)
from omnicoreagent.core.metrics import COUNTERS
from omnicoreagent.core.runtime import (
    builder,
    construction,
    execution,
    harness_tools,
    normalization,
    summaries,
    streaming,
)
from omnicoreagent.core.credentials import (
    register_config_credentials,
    register_environment,
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
from omnicoreagent.core.telemetry.context import current_telemetry_context



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

# A run in one of these has ended; nothing from outside reopens or rewrites it.
_ENDED_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout", "abandoned"})

# Seconds to wait before each further try of a memory read that failed: the
# store restarting (the support desk chaos run, 2026-10-07, Postgres "shutting
# down") is over in a moment, and a read has no side effect to repeat. A run
# whose store stays down fails with the store's error rather than going on
# without its history.
STORE_READ_RETRY_DELAYS = (0.5, 1.5)

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
        sub_agents: Optional[List[Any]] = None,
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
        An agent: a model, what it may use, and how it is run and recorded.

        Args:
            name: The agent's name, in its traces, records and workspace.
            system_instruction: What the agent is for; the start of its system
                prompt, before the runtime's own instructions.
            model_config: The model: ``{"provider": "openai", "model": "..."}``,
                with optional ``temperature``, ``max_tokens``, ``top_p``,
                ``reasoning_effort``, ``logprobs`` and ``api_key``, and
                ``azure_*`` or ``ollama_host`` for those providers (see
                Models). The key comes from ``LLM_API_KEY`` unless
                ``api_key`` is given.
            mcp_tools: MCP servers whose tools the agent may use: a list of
                dicts with ``name``, ``transport_type`` (``stdio``, ``sse`` or
                ``streamable_http``), and ``command``/``args`` or ``url``.
            local_tools: Your Python functions as tools: a ``ToolRegistry``
                (functions registered with ``@tools.register_tool``), or a
                list of ``Tool`` objects.
            sub_agents: Other ``OmniCoreAgent`` instances this one may hand a
                task to: each becomes a ``delegate_<name>`` tool.
            agent_config: The agent's settings (see Agent settings).
            memory_router: Where session history is kept:
                ``MemoryRouter("in_memory" | "redis" | "sql" | "mongodb")``;
                in memory by default.
            telemetry_store: A trace store to use instead of the built-in one,
                for example one shared by several agents.
            telemetry_recorder: A recorder to use instead of the built-in one.
            telemetry_stream: A stream of telemetry events to publish to.
            telemetry_exporters: Where traces are exported as they finish:
                OTLP, LangSmith, Opik or JSONL exporters.
            prompt_builder: Replaces how the system prompt is assembled.
            debug: Log each step in detail.
            telemetry_config: What a trace records, where, and for how long
                (see Telemetry settings).
            telemetry_payload_store: Where payloads too large to keep inline
                are stored, when offloading is on.
        """
        self.name = name
        self.system_instruction = system_instruction
        self.model_config = normalization.build_model_config(model_config)
        self.mcp_tools = normalization.build_mcp_tools(mcp_tools)
        # The credentials this process holds are never handed to a model or
        # written into a record, however a command comes to print them.
        register_config_credentials(self.model_config)
        register_config_credentials(self.mcp_tools)
        register_environment()
        self.local_tools = normalization.normalize_local_tools(local_tools)

        if sub_agents is not None and (
            not isinstance(sub_agents, (list, tuple))
            or not all(callable(getattr(agent, "run", None)) for agent in sub_agents)
        ):
            raise ValueError(
                "sub_agents must be a list of agents, e.g. sub_agents=[researcher]"
            )
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
        self._run_retention_started = False
        # Runs this process is executing now: the orphan sweep never claims
        # one of its own, whatever its heartbeat says.
        self._active_run_ids: set[str] = set()
        # Run IDs made by ``generate_run_id`` and not yet run.
        self._fresh_run_ids: set[str] = set()
        # Heartbeats of claimed orphans whose resume has not started its own.
        self._claim_heartbeats: dict[str, ClaimHeartbeat] = {}
        self._run_retention_last: Dict[str, Any] | None = None
        self._run_retention_automatic_runs = 0
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
        # Set by the lead's factory for a worker whose profile narrows its
        # tools; None offers every tool.
        self._only_tools: set[str] | None = None
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
        await self._check_worker_profiles()
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
        self._bind_privacy_to_model()
        self.local_tools = components.local_tools
        self._subagent_factory = components.subagent_factory
        if self._only_tools is not None:
            self.agent.tool_runtime_registry.only_tools = set(self._only_tools)

    async def _warm_up_model_client(self, *, in_trace: bool = False) -> None:
        """Load the model client off the event loop, before the heartbeat.

        A first load (`import litellm`) took 15-60 s in a fresh process and
        sat unexplained in the trace between the user's message and the next
        event (the rc8 gate, E7-4): in a run's trace it is its own span. It
        stays in the trace, so a run stopped while it loads keeps that trace
        on its record (the 0.5.0rc3 gate). A model connection of the
        application's own may have no async warm_up, or a plain one."""
        from omnicoreagent.core import llm

        warm_up = getattr(self.llm_connection, "warm_up", None)
        if not callable(warm_up):
            return
        span = None
        if in_trace and not getattr(llm, "_LITELLM_LOADED", True) and self.telemetry_recorder is not None:
            span = await self.telemetry_recorder.start_span(
                name="model.client.load",
                kind="runtime.control",
                actor=self._telemetry_actor(),
                input={"purpose": "load the model client"},
            )
        try:
            warmed = warm_up()
            if inspect.isawaitable(warmed):
                await warmed
        except BaseException:
            if span is not None:
                await self.telemetry_recorder.end_span(span.span_id, status="cancelled")
            raise
        if span is not None:
            await self.telemetry_recorder.end_span(span.span_id, status="ok")

    async def _check_worker_profiles(self) -> None:
        """A profile names only tools and MCP servers this agent has: a
        worker never gets more than its lead, and a misspelt name is refused
        here, not found missing by a worker mid-run."""
        factory = self._subagent_factory
        if factory is None or not getattr(factory, "profiles", None):
            return
        prepared = await self.agent.tool_runtime_registry.prepare_tools(local_tools=self.local_tools)
        tools = {tool.name for tool in prepared.list_tools()} if prepared is not None else set()
        servers = {str(server.get("name") or "") for server in self.mcp_tools or []}
        governed = getattr(self.agent, "governance_engine", None) is not None
        for profile in factory.profiles.values():
            if profile.policy and not governed:
                # Its rules would be dropped without a word.
                raise ValueError(
                    f"worker profile {profile.name!r} has rules, and governance is off: "
                    "turn governance on, or remove the profile's policy"
                )
            missing = sorted(set(profile.tools or ()) - tools - {"spawn_subagents"})
            if "spawn_subagents" in (profile.tools or ()):
                missing.append("spawn_subagents (workers do not spawn workers)")
            if missing:
                raise ValueError(
                    f"worker profile {profile.name!r} names tools this agent does not have: "
                    f"{', '.join(missing)}. Its tools: {', '.join(sorted(tools))}"
                )
            unknown = sorted(set(profile.mcp_servers or ()) - servers)
            if unknown:
                raise ValueError(
                    f"worker profile {profile.name!r} names MCP servers this agent does not have: "
                    f"{', '.join(unknown)}"
                )

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
        run_id = f"run_{uuid.uuid4().hex}"
        # A run ID made here cannot exist in the store yet, so the run that
        # starts with it need not look for one (see ``run``). Bounded: an ID
        # made and never run is forgotten.
        if len(self._fresh_run_ids) > 4096:
            self._fresh_run_ids.clear()
        self._fresh_run_ids.add(run_id)
        return run_id

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
        self._bind_privacy_to_model()
        self._bind_telemetry_components()

    def _bind_privacy_to_model(self) -> None:
        """What the provider is sent passes the agent's privacy filter (it
        redacts only with redact_model_io on)."""
        if self.llm_connection is not None:
            self.llm_connection.privacy_filter = self.privacy_filter

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
            # In words, not only a fingerprint: the trace is redacted whether
            # or not the model was, so a reader cannot tell from it.
            "privacy": _privacy_summary(self.privacy_filter.config),
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
        if self.can_execute and self._runs_on_host:
            warnings.append(
                {
                    "code": "host_execution_not_contained",
                    "message": "The local sandbox runs commands on this machine: "
                    "governed by policy but not isolated. A command can reach the "
                    "network and any file this user can.",
                }
            )
        if getattr(getattr(self, "agent", None), "tool_offload_refused_by_policy", False):
            warnings.append(
                {
                    "code": "tool_offload_refused_by_policy",
                    "message": "Tool offload is on, but the policy refuses "
                    "workspace.artifacts.read, so large results stay in context "
                    "instead of being offloaded; allow it to offload them.",
                }
            )
        return warnings

    @property
    def can_execute(self) -> bool:
        """Whether this agent has a sandbox that can run commands.

        Uses the same check governance uses before routing a command, so a
        tool is never offered that governance could not route. A ``local``
        sandbox counts: it runs commands, on the host. It answers once the
        agent is initialized (``await agent.initialize()``, or after its first
        run); before that it is ``False``, since it cannot wait to initialize.
        """
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        return bool(engine is not None and engine.sandbox_runtime_can_execute())

    @property
    def _runs_on_host(self) -> bool:
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        runtime = getattr(engine, "sandbox_runtime", None)
        return getattr(runtime, "execution_surface", "sandbox") == "host"

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
        _resume: Optional[Dict[str, Any]] = None,
        _resume_cause: Optional[Dict[str, Any]] = None,
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
        if not self._run_retention_started:
            # Finished run records past their window go the same way, once.
            self._run_retention_started = True
            await self._apply_run_retention(trigger="automatic")

        run_id = run_id or self.generate_run_id()
        # An ID this agent just made has no record to find: no read to know it.
        # Taken out of the set so a second run with the same ID does read.
        fresh_run_id = run_id in self._fresh_run_ids
        self._fresh_run_ids.discard(run_id)
        trace_context = None
        run_tracker = None
        keep_alive = None
        run_budgets = None
        retry_of = None
        if _resume is None and not fresh_run_id and supports_run_state(self.memory_router):
            # A known run ID: recover a run whose process died, refuse one that
            # is still live, or start a finished or failed one again. Read from
            # the router directly: initializing here would put an
            # initialization failure outside the run's trace.
            try:
                existing = await self.memory_router.get_run_state(run_id)
            except RunStateUnsupported:
                existing = None
            if existing is not None:
                problem = _not_resumable(existing, run_id)
                if problem is None:
                    _resume = existing
                elif existing["status"] in {"running", "awaiting_approval", "awaiting_budget"}:
                    raise ValueError(problem)
                else:
                    retry_of = existing
        if not self._initialized:
            # Before the trace: set-up (the model client, tools, MCP servers)
            # took 7-50 s inside a first run's trace with no span to say so,
            # and against its deadline (the rc7 gate, E7-4). A set-up that
            # fails is tried again inside the trace below, so the failure is
            # recorded on the run as before.
            try:
                await self.initialize()
            except Exception:  # noqa: BLE001 - recorded by the attempt in the trace
                pass
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
        # Who started this run: a background supervisor recovers its own runs,
        # so the orphan sweep must be able to tell them from interactive ones.
        started_by = current_telemetry_context()
        surface = (started_by.execution_surface if started_by else None) or "interactive"
        try:
            self._active_run_ids.add(run_id)
            trace_context = await self.telemetry_recorder.start_trace(
                name="agent.run",
                kind="agent.run",
                actor=self._telemetry_actor(),
                run_id=run_id,
                session_id=session_id,
                agent_id=self.name,
                provenance=provenance,
                metadata={**self._telemetry_metadata(), "tags": list(tags or [])},
                input={"query": query} if _resume is None else {"resumed_run_id": run_id},
            )
            if _resume is None:
                await self.telemetry_recorder.emit_event(
                    "user_message",
                    actor=TelemetryActor(type=ActorType.USER),
                    input={"message": query},
                )
            else:
                await self._close_dead_segments(
                    run_id, trace_context.trace_id, list(_resume.get("trace_ids") or [])
                )
                # Why: the state the run was left in, and who resumed it. A
                # sweeper passes the cause it read before it claimed the run,
                # since claiming rewrites the lease.
                why = {"trigger": "explicit", **(_resume_cause or resume_cause(_resume))}
                COUNTERS.inc(
                    "omniserve_runs_resumed_total",
                    trigger=why["trigger"],
                    cause=why["cause"],
                )
                await self.telemetry_recorder.emit_event(
                    "run_resumed",
                    actor=self._telemetry_actor(),
                    metadata={
                        **why,
                        "previous_trace_ids": list(_resume.get("trace_ids") or []),
                        "step": _resume.get("step"),
                        "approvals": [
                            {
                                key: approval.get(key)
                                for key in ("approval_id", "status", "approver", "note", "tool_name")
                            }
                            for approval in _resume.get("approvals") or []
                        ],
                        "budget_requests": [
                            {
                                key: request.get(key)
                                for key in (
                                    "request_id", "status", "approver", "note",
                                    "meter", "scope", "amount",
                                )
                            }
                            for request in _resume.get("budget_requests") or []
                        ],
                    },
                )
                for request in _resume.get("budget_requests") or []:
                    if request.get("status") in {"granted", "denied"}:
                        await self.telemetry_recorder.emit_event(
                            "budget_granted"
                            if request["status"] == "granted"
                            else "budget_denied",
                            actor=self._telemetry_actor(),
                            metadata={
                                key: request.get(key)
                                for key in (
                                    "request_id", "scope", "meter", "amount",
                                    "approver", "note",
                                )
                            },
                        )

            if not self._initialized:
                await self.initialize()  # failed before the trace: recorded here

            # The run's durable record lives in the chosen memory store.
            lease_seconds = int(self.agent_config.get("run_lease_seconds") or 60)
            if _resume is not None:
                run_tracker = RunTracker.from_record(
                    self.memory_router, _resume, lease_seconds=lease_seconds
                )
            elif retry_of is not None:
                run_tracker = RunTracker.new_attempt(
                    self.memory_router, retry_of, lease_seconds=lease_seconds
                )
            else:
                run_tracker = RunTracker(
                    self.memory_router,
                    run_id=run_id,
                    session_id=session_id,
                    agent_name=self.name,
                    agent_version=self.agent_config.get("agent_version"),
                    lease_seconds=lease_seconds,
                )
            if _resume is None:
                run_tracker.record["surface"] = surface
                # The request is part of the run from its first save. If the
                # memory store fails before the message reaches the session
                # history, a resume still has it (the support desk chaos run,
                # 2026-10-07, run_69673b9e answered a request nobody recorded).
                run_tracker.record["request"] = {
                    "content": self.privacy_filter.redact(query, boundary="memory"),
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            # Load the model client off the event loop before the heartbeat
            # starts: imported on the loop at a process's first call, it froze
            # it for seconds to minutes, the heartbeat stalled and a second
            # process took over a live run (the 0.5.0rc1 gate).
            # A model connection of the application's own may have no async
            # warm_up, or a plain one.
            run_tracker.attach_trace(trace_context.trace_id)
            await self._warm_up_model_client(in_trace=True)
            claim_beat = self._claim_heartbeats.pop(run_id, None)
            if claim_beat is not None:
                # The claim kept the lease alive through the slow start; the
                # tracker's first save continues from the version it left.
                claimed_version = await claim_beat.stop()
                if claimed_version is not None:
                    run_tracker.adopt_version(claimed_version)
            await run_tracker.start(trace_context.trace_id)
            if _resume is not None or retry_of is not None:
                # What a dead attempt of this run left running goes only now,
                # once this process has won the version-checked save that makes
                # the run its own. Before it, a second process that lost the
                # race removed the winner's sandbox (found merging the P6
                # tracks, 2026-10-07).
                await self._remove_run_sandboxes(run_id)
            # Keeps the heartbeat fresh during long model or tool calls.
            keep_alive = asyncio.create_task(run_tracker.keep_alive())

            blocked_response = None if _resume is not None else await execution.blocked_guardrail_response(
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

            # A spawned worker spends its lead's budgets (``WorkerBudgets``).
            run_budgets = getattr(self, "_lead_budgets", None) or self._build_run_budgets(
                run_id=run_id, session_id=session_id, resumed=_resume
            )
            if run_budgets is not None and (_resume is not None or retry_of is not None):
                # What an earlier attempt of this run held and never committed.
                await run_budgets.release_stale()
            async with run_tracker.active(), active_budgets(run_budgets):
                response = await self.agent.run(
                    **({"on_event": emit_delta} if delivery is not None else {}),
                    system_prompt=runtime_prompt,
                    query=query or "",
                    resume=_resume,
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
            # The run is over: its spend goes on its record and its own budget
            # counter is removed, so a request leaves nothing behind.
            budgets_spent = await run_budgets.settle() if run_budgets is not None else None
            run_error = formatted_response.pop("_run_error", None)
            if trace_status != TraceStatus.COMPLETED and run_error is None:
                # A run can end failed with no exception (a step limit, a
                # refusal, a budget): its record still says why. The support
                # desk chaos run (2026-10-07) found failed runs reading
                # `error: null`.
                run_error = {
                    "type": "RunFailed",
                    "message": str(formatted_response.get("response") or "")[:500],
                }
            if run_error is not None:
                run_error = self.privacy_filter.redact(run_error, boundary="public")
            await run_tracker.finish(
                "completed" if trace_status == TraceStatus.COMPLETED else "failed",
                usage=formatted_response.get("metric"),
                budgets=budgets_spent,
                error=run_error if trace_status != TraceStatus.COMPLETED else None,
                termination_reason=formatted_response.get("termination_reason"),
            )
            await self._settle_workers(run_id, run_tracker)
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
        except RunInterrupted as interrupted:
            # Stopped at a step boundary on request; resume() continues it.
            await run_tracker.finish("interrupted", usage=getattr(interrupted, "usage", None))
            await self.telemetry_recorder.emit_event(
                "run_interrupted", actor=self._telemetry_actor(), metadata={"run_id": run_id}
            )
            trace_finalizing = True
            await self.telemetry_recorder.end_trace(
                status=TraceStatus.SUSPENDED, output={"status": "interrupted"}
            )
            return self.privacy_filter.redact(
                {
                    "response": None,
                    "status": "interrupted",
                    "session_id": session_id,
                    "agent_name": self.name,
                    "run_id": run_id,
                    "trace_id": trace_context.trace_id,
                },
                boundary="public",
            )
        except RunAwaitingBudget as waiting:
            # The run cannot afford its next step. It keeps what it has done
            # and waits: grant_budget() and resume() carry it on, deny_budget()
            # ends it.
            await run_tracker.finish(
                "awaiting_budget", usage=getattr(waiting, "usage", None)
            )
            await self.telemetry_recorder.emit_event(
                "run_suspended",
                actor=self._telemetry_actor(),
                metadata={"budget_request": waiting.request},
            )
            trace_finalizing = True
            await self.telemetry_recorder.end_trace(
                status=TraceStatus.SUSPENDED,
                output={"status": "awaiting_budget", "budget": waiting.request["meter"]},
            )
            return self.privacy_filter.redact(
                {
                    "response": None,
                    "status": "awaiting_budget",
                    "budget_request": waiting.request,
                    "session_id": session_id,
                    "agent_name": self.name,
                    "run_id": run_id,
                    "trace_id": trace_context.trace_id,
                },
                boundary="public",
            )
        except RunSuspended as suspended:
            # Waiting for a person: this trace segment ends; resume() starts
            # the next one for the same run.
            await run_tracker.finish("awaiting_approval", usage=getattr(suspended, "usage", None))
            approvals = [_public_approval(a, run_tracker.record) for a in suspended.approvals]
            await self.telemetry_recorder.emit_event(
                "run_suspended",
                actor=self._telemetry_actor(),
                metadata={
                    "approvals": [
                        {key: a.get(key) for key in ("approval_id", "tool_name", "capability", "tool_call_id")}
                        for a in approvals
                    ]
                },
            )
            trace_finalizing = True
            await self.telemetry_recorder.end_trace(
                status=TraceStatus.SUSPENDED,
                output={"status": "awaiting_approval", "approval_count": len(approvals)},
            )
            return self.privacy_filter.redact(
                {
                    "response": None,
                    "status": "awaiting_approval",
                    "approvals": approvals,
                    "session_id": session_id,
                    "agent_name": self.name,
                    "run_id": run_id,
                    "trace_id": trace_context.trace_id,
                },
                boundary="public",
            )
        except asyncio.CancelledError as exc:
            # A deadline (run_with_timeout) cancels the run too; the record
            # says which, as the trace does.
            if current_stop_reason() == "timeout":
                await self._finish_run_record(
                    run_tracker,
                    "timeout",
                    TimeoutError("The run's deadline passed"),
                    budgets=run_budgets,
                    usage=getattr(exc, "usage", None),
                )
            else:
                await self._finish_run_record(run_tracker, "cancelled", exc, budgets=run_budgets)
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
            await self._finish_run_record(run_tracker, "failed", exc, budgets=run_budgets)
            if trace_context is not None and not trace_finalizing:
                await self._end_trace_after_failure(
                    trace_context, exc, status=TraceStatus.FAILED
                )
            raise

        finally:
            self._active_run_ids.discard(run_id)
            if keep_alive is not None:
                keep_alive.cancel()
            streaming.current_delivery.reset(delivery_token)

    async def prune_telemetry(self) -> Dict[str, Any]:
        """Apply the configured trace and payload retention now.

        Expired finished traces are removed first; payloads are then pruned
        by their own retention window, except any payload still referenced by
        a kept trace. Returns what was removed.
        """
        self._ensure_telemetry()
        return await self._apply_telemetry_retention(trigger="explicit")

    async def prune_runs(self) -> Dict[str, Any]:
        """Remove finished run records older than ``run_retention_days`` now
        (30 by default; ``None`` keeps every record). A run still waiting for
        a person or a resume is never removed. Returns what was removed."""
        return await self._apply_run_retention(trigger="explicit")

    async def _apply_run_retention(self, *, trigger: str) -> Dict[str, Any]:
        days = self.agent_config.get("run_retention_days", 30)
        summary: Dict[str, Any] = {
            "trigger": trigger,
            "retention_days": days,
            "runs_removed": 0,
            "error": None,
        }
        if days is not None:
            before = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            summary["before"] = before
            if not self.memory_router:
                self.memory_router = construction.default_memory_router()
            try:
                summary["runs_removed"] = await self.memory_router.delete_finished_run_states(
                    before=before, statuses=tuple(sorted(_PRUNABLE_RUN_STATUSES))
                )
            except RunStateUnsupported:
                summary["error"] = "This memory store keeps no run records"
            except Exception as exc:
                # Housekeeping never fails a run; the failure stays visible.
                summary["error"] = f"{exc.__class__.__name__}: {exc}"
                runtime_logger().warning(f"Run retention failed: {summary['error']}")
        summary["at"] = datetime.now(timezone.utc).isoformat()
        self._run_retention_last = summary
        if trigger == "automatic":
            self._run_retention_automatic_runs += 1
        if summary["runs_removed"]:
            runtime_logger().info(
                f"Run retention ({trigger}) removed {summary['runs_removed']} finished run record(s)"
            )
        return summary

    def telemetry_retention_status(self) -> Dict[str, Any]:
        """Report the retention policy and the most recent cleanup results:
        traces, payloads, and run records (``runs``)."""
        self._ensure_telemetry()
        trace_status = getattr(self.telemetry_store, "retention_status", None)
        payload_status = getattr(self.telemetry_payload_store, "retention_status", None)
        return {
            "trace_store": trace_status() if callable(trace_status) else None,
            "payload_store": payload_status() if callable(payload_status) else None,
            "last_cleanup": self._telemetry_retention_last,
            "automatic_runs": self._telemetry_retention_automatic_runs,
            "runs": {
                "retention_days": self.agent_config.get("run_retention_days", 30),
                "last_cleanup": self._run_retention_last,
                "automatic_runs": self._run_retention_automatic_runs,
            },
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
        self,
        run_tracker: Any,
        status: str,
        exc: BaseException,
        *,
        budgets: Any = None,
        usage: Any = None,
    ) -> None:
        """Record how a run ended; it must not replace the original error."""
        if run_tracker is None or run_tracker.record["status"] != "running":
            return
        try:
            budgets_spent = None
            if budgets is not None:
                budgets_spent = await complete_despite_cancellation(budgets.settle())
            await complete_despite_cancellation(
                run_tracker.finish(
                    status,
                    usage=usage if usage is not None else getattr(exc, "usage", None),
                    # Redacted as the response and the trace are.
                    error=self.privacy_filter.redact(
                        {"type": type(exc).__name__, "message": str(exc)},
                        boundary="public",
                    ),
                    budgets=budgets_spent,
                    # An exception that reached here is not the provider's
                    # (a provider's error ends the run in the model step).
                    termination_reason="internal_error" if status == "failed" else status,
                )
            )
            await complete_despite_cancellation(self._settle_workers(run_tracker.run_id, run_tracker))
        except Exception as record_exc:
            runtime_logger().warning(
                f"Could not record run {run_tracker.run_id} as {status}: "
                f"{record_exc.__class__.__name__}"
            )

    async def budget_status(self, run_id: str) -> List[Dict[str, Any]]:
        """Every budget covering a run: its limit, what a person granted on
        top of it, what is spent and held, and what remains.

        The application's, session's and agent's counters are read from the
        ledger (they are shared, and live on); a finished run's own counter is
        the one settled on its record. Empty when nothing is budgeted.
        """
        from omnicoreagent.core.budgets import METERS, BudgetScope

        # The budgets come from the policy, built when the agent initializes. A
        # process that only reads runs never ran a query, and read none: it
        # answered [] (found recording real footage of 0.4.3, 2026-09-29).
        if not self._initialized:
            await self.initialize()
        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        budgets = self._build_run_budgets(run_id=run_id, session_id=record.get("session_id"))
        if budgets is None or not budgets.enabled:
            return []
        settled = record.get("budgets") or {}
        # Charges the store could not take after the work was done: on the
        # record, not in the counters, so they are shown beside them.
        unrecorded: Dict[tuple, float] = {}
        for charge in record.get("unrecorded_charges") or settled.get("unrecorded") or []:
            slot = (charge.get("key"), charge.get("meter"))
            unrecorded[slot] = unrecorded.get(slot, 0.0) + float(charge.get("amount") or 0.0)
        entries: List[Dict[str, Any]] = []
        for meter in METERS:
            for scope, key, limit in budgets.limits(meter):
                usage = await budgets.ledger.usage(key)
                reserved = await budgets.ledger.reserved(key)
                # A person's grant raises what this budget allows, exactly as
                # enforcement counts it.
                granted = (await budgets.ledger.granted(key)).get(meter, 0.0)
                spent = usage.get(meter, 0.0)
                if scope == BudgetScope.REQUEST and record.get("status") not in {"running"}:
                    spent = (settled.get(scope.value) or {}).get(meter, spent)
                    # A finished run's grant was kept on its record.
                    granted = granted or (
                        (settled.get("granted") or {}).get(scope.value) or {}
                    ).get(meter, 0.0)
                entries.append(
                    {
                        "scope": scope.value,
                        "meter": meter,
                        "window": limit.window,
                        "key": key,
                        "limit": limit.limit,
                        "granted": granted,
                        "spent": spent,
                        "reserved": reserved.get(meter, 0.0),
                        "unrecorded": unrecorded.get((key, meter), 0.0),
                        "remaining": max(
                            0.0, limit.limit + granted - spent - reserved.get(meter, 0.0)
                        ),
                    }
                )
        return entries

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """A run's durable record, or None if it has none.

        The record is kept in the agent's memory store: status, step, usage,
        trace IDs, each tool call's state (arguments as a digest), and each
        approval with the arguments of the call it is for.
        """
        record = await self._run_record(run_id)
        if record is None:
            return None
        return _with_approval_arguments(record)

    async def _run_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The run's record exactly as stored, for the runtime's own use.

        Reading a record needs the memory store, not the model: a process
        that only reads runs and records outcomes needs no model key.
        """
        if not self.memory_router:
            self.memory_router = construction.default_memory_router()
        if not supports_run_state(self.memory_router):
            return None
        try:
            return await self.memory_router.get_run_state(run_id)
        except RunStateUnsupported:
            return None

    async def resume(
        self, run_id: str, on_event: Any = None, *, trigger: str = "explicit"
    ) -> Dict[str, Any]:
        """Continue a run: one waiting for approval once every approval is
        decided (see ``resolve_approval``), or one whose process stopped
        (its heartbeat is older than ``run_lease_seconds``). Completed tool
        calls never run again.

        ``trigger`` says who resumed it, for the run's trace and the
        ``omniserve_runs_resumed_total`` metric: a person or client
        (``explicit``), or the background supervisor retrying
        (``background_retry``)."""
        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        problem = _not_resumable(record, run_id)
        # A run that hit its deadline is stopped, not finished: its record
        # holds the calls it made and the conversation to that point, so it
        # continues as an interrupted run does (the chaos run, 2026-10-07,
        # had a client start a new run, which would repeat the refund the
        # timed-out run had made). `run(run_id=)` still starts a new attempt.
        if problem is not None and record["status"] != "timeout":
            raise ValueError(problem)
        try:
            return await self.run(
                None,
                session_id=record["session_id"],
                run_id=run_id,
                on_event=on_event,
                _resume=record,
                _resume_cause=(
                    None if trigger == "explicit" else {"trigger": trigger, **resume_cause(record)}
                ),
            )
        except RunStateConflict:
            # The version check lost: the orphan sweep (or another client)
            # claimed the run between our read and our first save. The run is
            # theirs and goes on there, so this caller is told so, as it is
            # when it arrives after the claim (the route answers 409).
            raise ValueError(
                f"Run {run_id} was taken by another process while this resume started"
            ) from None

    async def claim_orphaned_runs(
        self,
        *,
        limit: int = 10,
        max_recoveries: int = 3,
        decided_grace_seconds: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """Take over this agent's runs that nobody is working on, and say which.

        Two kinds of run are taken. An orphan is a run still ``running`` whose
        heartbeat is older than its lease, started by this agent (by name) and
        not by a background supervisor, which recovers its own. A decided run
        is one waiting for an approval or a budget decision that a person has
        made, and that nobody has resumed for ``decided_grace_seconds`` (the
        client that should have called ``resume`` was refused or is gone). A
        run with a decision still pending is left waiting.

        Each run is claimed with the record's own version check: the claim
        writes a new owner and a fresh heartbeat, and a second process that
        read the same record fails the check and skips it, so a run is never
        resumed twice. A client's ``resume`` that read the record before the
        claim fails its first save the same way and is refused. Each claim is
        then given to ``resume_claimed``. An orphan claimed ``max_recoveries``
        times and still not finished is ended ``failed`` instead (it is
        probably what kills its process); a decided run's claim does not count.

        Orphans were added after the support desk chaos run (2026-10-07), where
        runs whose process died stayed ``running`` until someone called
        ``resume``. Decided runs were added after the ramp at 100 users the
        same day, where 119 approved runs waited for a resume that the
        admission limit had answered 503.
        """
        if not self._initialized:
            await self.initialize()
        if not supports_run_state(self.memory_router):
            return []
        try:
            running = await self.memory_router.list_run_states(None, "running", 1000)
        except RunStateUnsupported:
            return []
        lease_seconds = int(self.agent_config.get("run_lease_seconds") or 60)
        claims: List[Dict[str, Any]] = []
        for record in running:
            if len(claims) >= limit:
                break
            run_id = record["run_id"]
            if (
                record.get("agent_name") != self.name
                or run_id in self._active_run_ids
                or record.get("surface") == "background"
                or not lease_expired(record)
            ):
                continue
            recoveries = int(record.get("recovery_count") or 0)
            if recoveries >= max_recoveries:
                await self.abandon_run(
                    run_id,
                    status="failed",
                    reason=(
                        f"recovered {recoveries} times and still did not finish; "
                        "it is probably what stops its process"
                    ),
                )
                continue
            # What the run looked like before the claim rewrites its lease.
            cause = {"trigger": "orphan_sweep", **resume_cause(record)}
            claim = await self._claim_record(record, cause, recoveries + 1, lease_seconds)
            if claim is not None:
                claims.append(claim)
        for status in ("awaiting_approval", "awaiting_budget"):
            if len(claims) >= limit:
                break
            try:
                waiting = await self.memory_router.list_run_states(None, status, 1000)
            except RunStateUnsupported:
                break
            for record in waiting:
                if len(claims) >= limit:
                    break
                decided = decided_waiting(record, grace_seconds=decided_grace_seconds)
                if (
                    decided is None
                    or record.get("agent_name") != self.name
                    or record["run_id"] in self._active_run_ids
                    or record.get("surface") == "background"
                ):
                    continue
                # The cause is the one an explicit resume would record, plus
                # the decision, so a trace says why nobody resumed it earlier.
                cause = {
                    "trigger": "orphan_sweep",
                    **resume_cause(record),
                    "decision": decided["decision"],
                    "decided_at": decided["decided_at"],
                }
                claim = await self._claim_record(
                    record, cause, int(record.get("recovery_count") or 0), lease_seconds
                )
                if claim is not None:
                    claims.append(claim)
        return claims

    async def _claim_record(
        self, record: Dict[str, Any], cause: Dict[str, Any], recovery_count: int, lease_seconds: int
    ) -> Optional[Dict[str, Any]]:
        """Claim one run through the record's version check; None if another
        process (or the client's own resume) got there first.

        A waiting run is claimed as ``running``: that is what makes a client
        resume arriving after the claim answer 409 (its heartbeat is current)
        rather than start the run a second time.
        """
        from uuid import uuid4

        run_id = record["run_id"]
        claimed = {key: value for key, value in record.items() if key != "version"}
        now = datetime.now(timezone.utc).isoformat()
        claimed.update(
            status="running",
            owner=f"owner_sweep_{uuid4().hex}",
            heartbeat_at=now,
            updated_at=now,
            lease_seconds=lease_seconds,
            recovery_count=recovery_count,
        )
        try:
            claimed["version"] = await self.memory_router.save_run_state(
                dict(claimed), expected_version=record["version"]
            )
        except RunStateConflict:
            return None  # another process claimed it first
        runtime_logger().info(
            f"Claimed run {run_id} ({cause.get('cause')}; owner {cause.get('previous_owner')}, "
            f"orphaned {cause.get('orphaned_seconds')}s)"
        )
        # The claim's lease starts now, but the resume can take longer than
        # a lease to start (a cold model client took 74 s against 60 s).
        beat = ClaimHeartbeat(self.memory_router, claimed, lease_seconds=lease_seconds)
        beat.start()
        self._claim_heartbeats[run_id] = beat
        return {"run_id": run_id, "record": claimed, "cause": cause}

    async def resume_claimed(self, claim: Dict[str, Any], on_event: Any = None) -> Dict[str, Any]:
        """Continue a run ``claim_orphaned_runs`` returned, with the same
        durable rules as ``resume``: completed calls never run again, and a
        call that did not finish becomes ``unknown_outcome`` unless its tool
        is idempotent."""
        record = claim["record"]
        try:
            return await self.run(
                None,
                session_id=record["session_id"],
                run_id=claim["run_id"],
                on_event=on_event,
                _resume=record,
                _resume_cause=claim["cause"],
            )
        finally:
            # The run took the heartbeat over when it started; if it failed
            # before that, the claim's heartbeat must not outlive it.
            await self.release_claim(claim)

    async def release_claim(self, claim: Dict[str, Any]) -> None:
        """Stop keeping a claim's lease alive (a claim nobody will resume, or
        one the run has taken over). Safe to call more than once."""
        beat = self._claim_heartbeats.pop(claim["run_id"], None)
        if beat is not None:
            await beat.stop()

    async def steer(
        self, run_id: str, message: str, *, sender: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a message to a run; it arrives as a user message at the run's
        next step boundary (or when a waiting or interrupted run resumes).

        The message is user input: the injection guardrail checks it first,
        and a blocked message is never queued.
        """
        from omnicoreagent.core.runs import update_from_outside
        from uuid import uuid4

        if not isinstance(message, str) or not message.strip():
            raise ValueError("The steering message is empty")
        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        if record["status"] not in {"running", "awaiting_approval", "interrupted"}:
            raise ValueError(f"Run {run_id} is {record['status']}; it cannot be steered")
        if self.guardrail is not None:
            check = self.guardrail.check(message)
            if not check.is_safe:
                runtime_logger().warning(f"Steering message for {run_id} blocked by guardrail")
                return {
                    "status": "blocked",
                    "guardrail_result": check.to_dict() if hasattr(check, "to_dict") else None,
                }
        entry = {
            "id": f"steer_{uuid4().hex}",
            "content": message,
            "sender": sender,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivered": False,
        }
        await update_from_outside(
            self.memory_router, run_id, lambda r: r.setdefault("inbox", []).append(entry)
        )
        return {"status": "queued", "message_id": entry["id"]}

    async def training_records(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        trace_ids: List[str] | None = None,
        limit: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Finished runs as one record each, for a trainer or an evaluator.

        Each record holds what the model was sent at every step, what it
        produced (its token details when they were recorded), what the tools
        answered, the policy that served it, the run's totals, and the
        outcomes attached to it. A run that paused and resumed is one record
        with the steps of every trace segment. A run still running or
        waiting, or one recorded without model prompts, is left out.
        """
        self._ensure_telemetry()
        if trace_ids:
            candidates = [t for t in [await self.telemetry_store.get_trace(i) for i in trace_ids] if t]
        else:
            trace_filter = TraceFilter(run_id=run_id, session_id=session_id)
            candidates = await self.telemetry_store.list_traces(trace_filter)
        records = []
        seen: set[str] = set()
        for trace in candidates:
            if not any(span.kind == "agent.run" for span in trace.spans):
                continue
            # A run that paused for a person, or was recovered, is several
            # traces: it is read once, whole, from its durable record.
            key = trace.run_id or trace.trace_id
            if key in seen:
                continue
            seen.add(key)
            run = await self._run_record(trace.run_id) if trace.run_id else None
            if run is not None:
                if run.get("status") not in _FINISHED_RUN_STATUSES:
                    continue
                trace_ids = list(run.get("trace_ids") or [trace.trace_id])
            else:
                # No record (pruned, or kept in another process's memory): the
                # run is every trace carrying its id, in order, finished if its
                # last one is. Reading one trace skipped a resumed run whole
                # (its paused segment is unfinished; the 0.5.0rc1 gate).
                related = [trace]
                if trace.run_id:
                    related = [
                        t
                        for t in await self.telemetry_store.list_traces(TraceFilter(run_id=trace.run_id))
                        if any(span.kind == "agent.run" for span in t.spans)
                    ] or [trace]
                related.sort(key=lambda t: t.started_at)
                last = related[-1]
                if str(getattr(last.status, "value", last.status)) in _UNFINISHED_TRACE_STATUSES:
                    continue
                trace_ids = [t.trace_id for t in related]
            segments = [
                t
                for t in [
                    await self.get_trajectory(trace_id=trace_id, include_children=False)
                    for trace_id in trace_ids
                ]
                if t
            ]
            record = _training_record(segments, run) if segments else None
            if record is not None:
                records.append(record)
            if limit is not None and len(records) >= limit:
                break
        return records

    async def record_outcome(
        self,
        run_id: str,
        *,
        source: str,
        reward: float | None = None,
        label: str | None = None,
        detail: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Record what a run turned out to be worth, whenever that is known.

        A run's own result is not its outcome: a pull request is merged an
        hour later, a customer accepts an answer the next day, CI runs the
        tests afterwards. ``source`` says who is reporting it (``github``,
        ``reviewer``, an evaluator's name), ``reward`` is the number a
        trainer or an evaluator uses, ``label`` is what it is called, and
        ``detail`` is anything else worth keeping. A run may gather several.
        The outcome goes on the run's record and into its trace.
        """
        from omnicoreagent.core.runs import update_from_outside
        from omnicoreagent.core.telemetry.models import telemetry_id, utc_now

        if not str(source or "").strip():
            raise ValueError("record_outcome needs a source: who reports this outcome")
        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        outcome = {
            "outcome_id": telemetry_id("outcome"),
            "reward": float(reward) if reward is not None else None,
            "label": label,
            "source": str(source).strip(),
            "detail": dict(detail or {}),
            "recorded_at": utc_now().isoformat(),
        }
        await update_from_outside(
            self.memory_router,
            run_id,
            lambda current: current.setdefault("outcomes", []).append(dict(outcome)),
        )
        trace_ids = record.get("trace_ids") or []
        if trace_ids:
            await self._record_outcome_event(trace_ids[-1], run_id, outcome)
        return outcome

    async def _record_outcome_event(
        self, trace_id: str, run_id: str, outcome: Dict[str, Any]
    ) -> None:
        """Put the outcome in the run's trace, which has long since ended."""
        from omnicoreagent.core.telemetry.models import (
            ActorType,
            TelemetryActor,
            TelemetryEvent,
        )

        self._ensure_telemetry()
        event = TelemetryEvent(
            trace_id=trace_id,
            event_type="run_outcome",
            actor=TelemetryActor(type=ActorType.SYSTEM, name=outcome["source"]),
            output=dict(outcome),
            metadata={"run_id": run_id, "outcome_id": outcome["outcome_id"]},
        )
        try:
            await self.telemetry_store.append_event(trace_id, event)
            flush = getattr(self.telemetry_store, "flush", None)
            if flush is not None:
                await flush()
        except Exception as exc:  # noqa: BLE001 - the record holds it regardless.
            runtime_logger().warning(f"Outcome of {run_id} not written to its trace: {exc}")

    async def _close_dead_segments(
        self,
        run_id: str,
        current_trace_id: str,
        previous_trace_ids: list[str] | None = None,
    ) -> None:
        """Close the earlier segments of a resumed run that never ended.

        A process killed mid-segment cannot end its own trace. The run's record
        said `completed` once a later segment finished it, but the trace
        listing showed the killed segment as `running` with no end until
        retention removed it (0.5.0, run_b6638561). The resume closes it as
        `interrupted`, ended at its last event. A resume happens only after
        the lease lapsed or a person decided, so no live process owns the
        segment. A failure here never stops the resume.

        The run's record names its segments (``previous_trace_ids``), so only
        those are read. This ran on every resume through ``list_traces``,
        which walks every trace in the store, and about 5% of a 30-user
        profile went there, growing with the store (the support desk ramp,
        2026-10-07). A record from before segments were named has none, and
        only then is the run's listing used.
        """
        try:
            if previous_trace_ids:
                segments = [
                    trace
                    for trace in [
                        await self.telemetry_store.get_trace(trace_id)
                        for trace_id in dict.fromkeys(previous_trace_ids)
                        if trace_id != current_trace_id
                    ]
                    if trace is not None
                ]
            else:
                segments = await self.telemetry_store.list_traces(TraceFilter(run_id=run_id))
            for trace in segments:
                if (
                    trace.trace_id == current_trace_id
                    or trace.status != TraceStatus.RUNNING
                    or trace.ended_at is not None
                ):
                    continue
                # The last event is the last thing the segment is known to
                # have done, so the segment ends there.
                moments = [e.timestamp for e in trace.events if e.timestamp]
                await self.telemetry_store.update_trace(
                    trace.trace_id,
                    {
                        "status": TraceStatus.INTERRUPTED.value,
                        "ended_at": max(moments, default=trace.started_at),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - the record holds the truth regardless.
            runtime_logger().warning(f"Earlier segments of {run_id} not closed: {exc}")

    async def abandon_run(self, run_id: str, *, status: str, reason: str) -> Dict[str, Any] | None:
        """Close a run this agent did not finish itself.

        For whoever ended it from outside: a background run cancelled while it
        waited, or failed because its worker died. The record says ``status``
        (``cancelled``, ``failed`` or ``timeout``) and why, and the request's
        own budget counters are released, keeping what it spent on the
        record. A run that already ended is left as it is. Found on the
        steward's server: such runs stayed "running" or "awaiting_budget" for
        good, each with its counter still in the ledger.
        """
        from omnicoreagent.core.runs import update_from_outside

        if status not in {"cancelled", "failed", "timeout"}:
            raise ValueError("status must be cancelled, failed or timeout")
        # Its budgets come from the policy, built when the agent initializes;
        # uninitialized, the run was closed and its holds left standing.
        if not self._initialized:
            await self.initialize()
        record = await self._run_record(run_id)
        if record is None or record.get("status") in _ENDED_RUN_STATUSES:
            return record
        spent = None
        budgets = self._build_run_budgets(run_id=run_id, session_id=record.get("session_id"))
        if budgets is not None:
            await budgets.release_stale()
            spent = await budgets.settle()

        def close(current: dict[str, Any]) -> None:
            if current.get("status") in _ENDED_RUN_STATUSES:
                return
            current["status"] = status
            current["error"] = {"type": "RunEndedOutside", "message": reason}
            if status in SIDE_EFFECT_STATUSES:
                note_side_effects(current)
            if spent:
                current["budgets"] = spent
            # A request no one will resume into is closed: granting it later
            # added headroom to a budget, a shared one too (the 0.5.0rc1 gate).
            for request in current.get("budget_requests") or []:
                if request.get("status") == "pending":
                    request["status"] = "abandoned"

        await update_from_outside(self.memory_router, run_id, close)
        await self._remove_run_sandboxes(run_id)
        await self._settle_workers(run_id)
        return await self.get_run(run_id)

    async def _settle_workers(self, lead_run_id: str, tracker: Any = None) -> None:
        """Mark the workers of an ended lead run that nothing will resume.

        A lead process that died left the workers it had started `running`,
        and they stayed so until someone abandoned each by hand (the 0.5.0
        known issue). Once the lead's run has ended, a worker still `running`
        whose own lease has lapsed has no process and no lead to resume it, so
        it is marked `abandoned` and the reason names the lead's run. A worker
        with a live lease is never touched: its process is still working. A
        failure here never changes how the lead ended.
        """
        from omnicoreagent.core.runs import lease_expired, update_from_outside

        try:
            if tracker is not None and tracker.enabled and not tracker.record.get("delegations"):
                # The run that just ended wrote its own record, delegations
                # included, and named none: there is no worker to settle, and
                # reading the record back only to learn that cost a database
                # round trip at the end of every run (the support desk ramp,
                # 2026-10-07).
                return
            lead = await self._run_record(lead_run_id)
            if lead is None or lead.get("status") not in _ENDED_RUN_STATUSES:
                return
            for delegation in lead.get("delegations") or []:
                child_id = delegation.get("child_run_id")
                child = await self._run_record(child_id) if child_id else None
                if child is None or child.get("status") != "running" or not lease_expired(child):
                    continue

                def close(current: dict[str, Any]) -> None:
                    # Read again under the version: it may have been taken over.
                    if current.get("status") != "running" or not lease_expired(current):
                        return
                    current["status"] = "abandoned"
                    current["error"] = {
                        "type": "LeadRunEnded",
                        "message": (
                            f"Its lead run {lead_run_id} ended while this worker's "
                            f"process was gone"
                        ),
                    }

                await update_from_outside(self.memory_router, child_id, close)
                await self._remove_run_sandboxes(child_id)
        except Exception as exc:  # noqa: BLE001 - the lead's own record is already written.
            runtime_logger().warning(f"Workers of {lead_run_id} not settled: {exc}")

    async def _remove_run_sandboxes(self, run_id: str) -> None:
        """Remove sandboxes a dead process of this run left running.

        After kill -9 a run's sandbox kept running for good, and the only
        cleanup removed every agent's (the 0.5.0rc1 gate). A provider that can
        find a run's sandboxes removes them; a failure here never stops the run.
        """
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        cleanup = getattr(getattr(engine, "sandbox_runtime", None), "cleanup_orphans", None)
        if cleanup is None or "run_id" not in inspect.signature(cleanup).parameters:
            return
        try:
            removed = await cleanup(run_id=run_id)
            if removed:
                runtime_logger().info(f"Removed {removed} sandbox(es) left by run {run_id}")
        except Exception as exc:
            runtime_logger().warning(
                f"Could not remove sandboxes left by run {run_id}: {exc.__class__.__name__}"
            )

    async def interrupt(self, run_id: str) -> Dict[str, Any]:
        """Ask a running run to stop at its next step boundary; it becomes
        ``interrupted`` and ``resume`` continues it."""
        from omnicoreagent.core.runs import update_from_outside

        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        if record["status"] != "running":
            raise ValueError(
                f"Run {run_id} is {record['status']}; only a running run can be interrupted"
            )
        await update_from_outside(
            self.memory_router, run_id, lambda r: r.__setitem__("interrupt_requested", True)
        )
        return {"status": "interrupt_requested", "run_id": run_id}

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
        decided = await decide(
            self.memory_router,
            run_id,
            approval_id,
            decision=decision,
            approver=approver,
            note=note,
            arguments=arguments,
        )
        if decided.get("delegated_run_id"):
            # The ask was a child's, mirrored here: the decision is theirs
            # too, recorded where the child keeps its runs (a named child may
            # have its own store; a spawned worker shares the lead's).
            child = next(
                (c for c in self.sub_agents or [] if getattr(c, "name", None) == decided.get("delegated_name")),
                None,
            )
            store = getattr(child, "memory_router", None) or self.memory_router
            await decide(
                store,
                decided["delegated_run_id"],
                decided["delegated_approval_id"],
                decision=decision,
                approver=approver,
                note=note,
                arguments=arguments,
            )
        return decided

    async def grant_budget(
        self,
        run_id: str,
        *,
        amount: Optional[float] = None,
        approver: str,
        note: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add to the budget a waiting run ran out of, so it can carry on.

        The policy is not changed: this is a recorded exception to one budget,
        with the name of whoever made it. ``amount`` defaults to what the run
        said it was short of. ``resume(run_id)`` then continues the work.
        """
        return await self._decide_budget(
            run_id,
            request_id=request_id,
            granted=True,
            amount=amount,
            approver=approver,
            note=note,
        )

    async def deny_budget(
        self,
        run_id: str,
        *,
        approver: str,
        note: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refuse a waiting run's budget: ``resume(run_id)`` ends it cleanly."""
        return await self._decide_budget(
            run_id,
            request_id=request_id,
            granted=False,
            amount=None,
            approver=approver,
            note=note,
        )

    async def _decide_budget(
        self,
        run_id: str,
        *,
        request_id: Optional[str],
        granted: bool,
        amount: Optional[float],
        approver: str,
        note: Optional[str],
    ) -> Dict[str, Any]:
        from omnicoreagent.core.runs import update_from_outside

        if not self._initialized:
            await self.initialize()
        if not supports_run_state(self.memory_router):
            raise LookupError(f"No run {run_id}: the memory store keeps no run state")
        record = await self._run_record(run_id)
        if record is None:
            raise LookupError(f"No run {run_id}")
        if record.get("status") != "awaiting_budget":
            raise LookupError(
                f"Run {run_id} is not waiting for a budget decision (it is {record.get('status')})"
            )
        pending = [
            request
            for request in record.get("budget_requests", [])
            if request["status"] == "pending"
            and (request_id is None or request["request_id"] == request_id)
        ]
        if not pending:
            raise LookupError(f"Run {run_id} is not waiting for a budget decision")
        request = pending[-1]
        given = float(request["shortfall"] if amount is None else amount)
        if granted:
            await BudgetLedger(self.memory_router).grant(
                request["key"],
                request["meter"],
                given,
                approver=approver,
                note=note,
            )

        def decide(stored: dict[str, Any]) -> None:
            for item in stored.get("budget_requests", []):
                if item["request_id"] == request["request_id"]:
                    item.update(
                        status="granted" if granted else "denied",
                        approver=approver,
                        note=note,
                        amount=given if granted else 0.0,
                        # The sweep waits a grace period from here for a
                        # client to resume before it resumes the run itself.
                        decided_at=datetime.now(timezone.utc).isoformat(),
                    )

        # The decision is recorded on the run; the run's own trace records it
        # when it continues, as an approval's decision is.
        await update_from_outside(self.memory_router, run_id, decide)
        for waiting in request.get("delegated") or []:
            # The request was workers', mirrored here: the decision is theirs
            # too, and the grant is already on the ledger they share.
            def decide_child(stored: dict[str, Any], child_request_id=waiting.get("request_id")) -> None:
                for item in stored.get("budget_requests", []):
                    if item["request_id"] == child_request_id:
                        item.update(
                            status="granted" if granted else "denied",
                            approver=approver,
                            note=note,
                            amount=given if granted else 0.0,
                        )

            await update_from_outside(self.memory_router, waiting["run_id"], decide_child)
        return {
            "run_id": run_id,
            "request_id": request["request_id"],
            "status": "granted" if granted else "denied",
            "meter": request["meter"],
            "scope": request["scope"],
            "amount": given if granted else 0.0,
            "approver": approver,
            "note": note,
        }

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
            records = await self.memory_router.list_run_states(session_id, status, limit)
        except RunStateUnsupported:
            return []
        # As get_run: an approver deciding from the list sees each call.
        return [_with_approval_arguments(record) for record in records]

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

    def _build_run_budgets(
        self, *, run_id: str, session_id: str | None, resumed: dict | None = None
    ):
        """The budgets covering this run, or nothing when none are set."""
        engine = getattr(getattr(self, "agent", None), "governance_engine", None)
        policy = getattr(engine, "policy", None)
        budgets = getattr(policy, "budgets", None)
        if budgets is None:
            return None
        refused = {
            (request["key"], request["meter"])
            for request in (resumed or {}).get("budget_requests", [])
            if request.get("status") == "denied"
        }
        return RunBudgets(
            BudgetLedger(self.memory_router),
            budgets,
            run_id=run_id,
            session_id=session_id,
            agent_name=self.name,
            telemetry_recorder=self.telemetry_recorder,
            refused=refused,
        )

    async def _run_summary(self, trace_id: str, *, whole_run: bool = True) -> Dict[str, Any]:
        """Totals for this trace segment, with its subagents' tokens and cost
        added; for a resumed run, ``whole_run`` adds the totals of every
        segment (see ``_whole_run``)."""
        recorder = self.telemetry_recorder
        # Totals only read the trace; a copy of it would be most of the work.
        trace = await recorder.peek_trace(trace_id)
        if trace is None:
            return {"run_summary": None, "final_model_response_event_id": None}
        summary = summarize_trace(trace)
        combined_tokens = dict(summary["tokens"])
        combined_cost = summary["estimated_cost_usd"] or 0.0
        # Whether anything was priced at all: if nothing was, the combined
        # cost is unknown (None), as the run's own is, not 0.0.
        priced = summary["estimated_cost_usd"] is not None
        cost_complete = summary["cost_complete"] or summary["model_calls"]["total"] == 0
        for child_id in summary["subagents"]["child_trace_ids"]:
            child = await recorder.peek_trace(child_id)
            if child is None:
                cost_complete = False
                continue
            child_summary = summarize_trace(child)
            for key, value in child_summary["tokens"].items():
                combined_tokens[key] = combined_tokens.get(key, 0) + value
            combined_cost += child_summary["estimated_cost_usd"] or 0.0
            priced = priced or child_summary["estimated_cost_usd"] is not None
            if child_summary["model_calls"]["total"] and not child_summary["cost_complete"]:
                cost_complete = False
        summary["including_subagents"] = {
            "tokens": combined_tokens,
            "estimated_cost_usd": round(combined_cost, 10) if priced else None,
            "cost_complete": cost_complete,
        }
        previous = next(
            (
                list(event.metadata.get("previous_trace_ids") or [])
                for event in trace.events
                if event.event_type == "run_resumed"
            ),
            [],
        )
        if whole_run and previous:
            summary["whole_run"] = await self._whole_run(summary, trace, previous)
        return {
            "run_summary": summary,
            "final_model_response_event_id": final_model_response_event_id(trace),
        }

    async def _whole_run(
        self, segment: Dict[str, Any], trace: TelemetryTrace, previous: List[str]
    ) -> Dict[str, Any]:
        """A resumed run's totals over all its segments.

        The summary a segment ends with covers that segment alone, so a run
        resumed once read as one model call and half its tokens and time
        (found reading the support desk's resumed runs, 2026-10-08).
        ``duration_ms`` is the time the segments were running, as the run
        story sums it; ``elapsed_ms`` is from the first segment's start, the
        wait for a person included.
        """
        recorder = self.telemetry_recorder
        summaries: List[Dict[str, Any]] = []
        starts = [trace.started_at]
        for trace_id in previous:
            earlier = await recorder.peek_trace(trace_id)
            if earlier is None:
                continue  # pruned; the count below says so
            starts.append(earlier.started_at)
            summaries.append((await self._run_summary(trace_id, whole_run=False))["run_summary"])
        summaries.append(segment)
        total: Dict[str, Any] = {}
        for each in summaries:
            total = _add_totals(total, each)
        ended = trace.ended_at or datetime.now(timezone.utc)
        return {
            "segments": len(summaries),
            "segments_missing": len(previous) + 1 - len(summaries),
            # Step numbers continue across segments, so the last is the count.
            "steps": segment["steps"],
            "model_calls": total["model_calls"],
            "tokens": total["tokens"],
            "estimated_cost_usd": total["estimated_cost_usd"],
            "cost_complete": all(each["cost_complete"] for each in summaries),
            "model_latency_ms": total["model_latency_ms"],
            "duration_ms": round(total["duration_ms"], 3),
            "elapsed_ms": round((ended - min(starts)).total_seconds() * 1000, 3),
            "including_subagents": total["including_subagents"],
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
            await self._write_history(
                run, role, stored_content, stored_metadata, session_id
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
            await self._write_history(
                run, role, stored_content, stored_metadata, session_id
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

    async def _write_history(
        self,
        run: Any,
        role: str,
        content: str,
        metadata: dict | None,
        session_id: str | None,
    ) -> None:
        """Write one message to the session's history and to the run's record.

        A store that fails raises, and the run fails with its error. The
        message goes on the run's record first, so a failed history write does
        not also lose what the model said (a lost answer cannot be asked for
        again; the record is what a person reads to see what happened).
        """
        if run is not None:
            await run.add_message(
                {"role": role, "content": content, "metadata": metadata}
            )
        await self.memory_router.store_message(role, content, metadata, session_id)

    async def _read_session_messages(
        self, session_id: str, agent_name: str | None
    ) -> list[dict[str, Any]]:
        """Read the session's history, trying again when the store errs.

        Only reads are repeated: a read has nothing to duplicate, while a write
        that failed after it committed would be stored twice. When the store
        stays down the last error is raised and the run fails with it.
        """
        for delay in STORE_READ_RETRY_DELAYS:
            try:
                return await self.memory_router.get_messages(session_id, agent_name)
            except Exception as exc:
                runtime_logger().warning(
                    f"Memory read failed ({exc.__class__.__name__}); trying again in {delay}s"
                )
                await asyncio.sleep(delay)
        return await self.memory_router.get_messages(session_id, agent_name)

    async def _get_messages_with_telemetry(
        self,
        session_id: str,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.telemetry_recorder is None:
            messages = await self._read_session_messages(session_id, agent_name)
            await _keep_run_history(messages)
            return messages
        span = await self.telemetry_recorder.start_span(
            name="memory.read",
            kind="memory.read",
            actor=TelemetryActor(type=ActorType.MEMORY),
            input={"session_id": session_id, "agent_name": agent_name},
        )
        try:
            messages = await self._read_session_messages(session_id, agent_name)
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
        """Every tool the model is offered: MCP, local and built-in tools, and
        a ``delegate_<name>`` tool for each of ``sub_agents``."""
        if not self._initialized:
            await self.initialize()

        runtime_local_tools = self.local_tools
        if self.agent and hasattr(self.agent, "tool_runtime_registry"):
            runtime_local_tools = await self.agent.tool_runtime_registry.prepare_tools(
                local_tools=self.local_tools
            )

        tools = harness_tools.available_tools(self.mcp_client, runtime_local_tools)
        for child in self.sub_agents or []:
            # The same definition the model is offered (native_catalog).
            from omnicoreagent.core.tools.native_catalog import delegate_tool_schema

            schema = delegate_tool_schema(child)
            tools.append(
                {
                    "name": f"delegate_{child.name}",
                    "description": f"Delegate to {child.name}. {getattr(child, 'system_instruction', '')}",
                    "inputSchema": schema,
                    "type": "subagent",
                }
            )
        return tools

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
        """This agent's latest trace in a session. A sub-agent shares its
        lead's session; its traces are the lead's trace family, not this."""
        traces = await self.list_telemetry_traces(
            session_id=session_id,
            agent_id=self.name,
            normalize=normalize,
        )
        return traces[-1] if traces else None

    async def get_telemetry_trace(
        self,
        trace_id: str,
        *,
        normalize: bool = False,
    ) -> Dict[str, Any] | None:
        """The raw trace of one trace segment, as a dict: its spans and
        events as recorded, or ``None`` if it is not stored (never recorded,
        or pruned after ``telemetry_config["retention_days"]``).

        ``normalize=True`` sorts spans by start and events by sequence, and
        marks capture gaps and missing references. For a readable story use
        ``get_trajectory`` (one segment) or ``get_run_trajectory`` (a whole
        run, across pauses and resumes).
        """
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
        # A store that indexes its traces answers without reading them all.
        indexed = getattr(self.telemetry_store, "payload_references", None)
        if callable(indexed):
            return await indexed()
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
        """Stored traces matching every filter given, oldest first (by start
        time, then trace ID), as dicts.

        Filter with keywords or one ``TraceFilter``, not both. A run that
        paused and resumed has one trace per segment; ``run_id=`` lists them
        all. ``normalize`` is as in ``get_telemetry_trace``.
        """
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
        """One trace segment as an ordered trajectory: steps, model calls, tool
        calls and their observations, from request to answer or pause.

        A run that paused and resumed has one segment per stretch of work;
        ``get_run_trajectory(run_id)`` gives the whole run. Look up by
        ``trace_id`` (exact) or ``run_id`` (the latest segment of that run;
        the others are listed in ``other_trace_ids_for_run``). Delegated child runs are nested under the
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
        totals = trajectory.get("totals")
        if isinstance(totals, dict) and totals.get("including_subagents") is None:
            # A finished segment carries its children's totals; one that
            # paused has them only in its trace (the 0.5.0rc1 and rc2 gates).
            summary = (await self._run_summary(trace.trace_id))["run_summary"]
            if summary is not None:
                totals["including_subagents"] = summary.get("including_subagents")
        if run_id is not None:
            trajectory["other_trace_ids_for_run"] = other_trace_ids
        return trajectory

    async def get_run_trajectory(self, run_id: str) -> Dict[str, Any] | None:
        """One durable run as a single story across its trace segments (each
        pause, resume, recovery, or new attempt is a segment), with totals
        summed over the segments. The run's saved conversation is not
        included.

        A segment whose trace is no longer stored (traces are pruned after
        ``telemetry_config["retention_days"]``, run records after
        ``run_retention_days``) has ``trace_kept: False``; ``traces_missing``
        counts them, and totals cover only the traces still kept, so they are
        empty rather than zero when none is. The record's ``usage`` remains.
        """
        record = await self._run_record(run_id)
        if record is None:
            return None
        segments = []
        for trace_id in record.get("trace_ids") or []:
            trajectory = await self.get_trajectory(trace_id=trace_id)
            segments.append(
                {
                    "trace_id": trace_id,
                    "status": (trajectory or {}).get("status"),
                    "trajectory": trajectory,
                    "trace_kept": trajectory is not None,
                }
            )
        kept = [segment for segment in segments if segment["trace_kept"]]
        totals: Dict[str, Any] = {}
        including: Dict[str, Any] = {}
        outcomes: Dict[str, Any] = {}
        for segment in kept:
            trajectory = segment["trajectory"]
            segment_totals = trajectory.get("totals") or {}
            totals = _add_totals(totals, segment_totals)
            # get_trajectory fills a paused segment's children's totals from
            # its trace: taking its own totals alone dropped a sub-agent it
            # ran before the pause (the 0.5.0rc1 gate counted 10,257 tokens
            # of a run's 13,301).
            segment_including = segment_totals.get("including_subagents")
            including = _add_totals(
                including,
                segment_including
                or {
                    "tokens": segment_totals.get("tokens") or {},
                    "estimated_cost_usd": segment_totals.get("estimated_cost_usd") or 0.0,
                    "cost_complete": segment_totals.get("cost_complete", True),
                },
            )
            calls = [c for step in trajectory.get("steps") or [] for c in step["tool_calls"]]
            for call in [*calls, *(trajectory.get("tool_calls_outside_steps") or [])]:
                # A call waiting for approval appears again when it runs on
                # resume: count it once, with its latest outcome.
                outcomes[call["tool_call_id"]] = call.get("outcome")
        if kept:
            by_outcome = {key: 0 for key in (totals.get("tool_calls") or {}).get("by_outcome", {})}
            for outcome in outcomes.values():
                if outcome is not None:
                    by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            totals["tool_calls"] = {"total": len(outcomes), "by_outcome": by_outcome}
            # A step that paused and the same step resumed share a number:
            # count each step once, not once per segment.
            if "steps" in totals:
                totals["steps"] = len(
                    {
                        step.get("step")
                        for segment in kept
                        for step in segment["trajectory"].get("steps") or []
                    }
                )
            totals["including_subagents"] = including
        return {
            "run_id": run_id,
            "session_id": record.get("session_id"),
            "agent_name": record.get("agent_name"),
            "status": record.get("status"),
            "attempt": record.get("attempt"),
            "previous_attempts": record.get("previous_attempts") or [],
            "segments": segments,
            "traces_missing": len(segments) - len(kept),
            "totals": totals,
            "tool_calls": record.get("tool_calls") or [],
            "approvals": record.get("approvals") or [],
            "usage": record.get("usage") or {},
        }

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
        """The store's position now: a cursor to pass to
        ``stream_telemetry_after`` or ``get_telemetry_events_after`` to get
        only events recorded from here on.

        Take it before starting a run to follow that run from its first
        event. Cursors are opaque strings that increase with every event.
        """
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
        """Yield telemetry events after ``cursor``, then keep yielding new
        ones as they are recorded; it does not end on its own.

        Events come in the order they were recorded, each once, filtered to
        the trace, run, session or task and the ``event_types`` given.
        ``cursor=None`` starts from the first stored event. Each event
        carries its ``stream_cursor``: keep the last one to resume after a
        disconnect. A reader that falls 1,000 events behind is stopped with
        an error rather than silently skipping events.
        """
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
        """The telemetry events stored after ``cursor``, as a list, in the
        order they were recorded, filtered like ``stream_telemetry_after``;
        it returns at once instead of waiting for more.

        ``cursor=None`` returns every stored event in scope. Poll with the
        last event's ``stream_cursor`` to get only what is new.
        """
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


def _public_approval(approval: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """What an approver needs to decide: the call as the model made it."""
    # A worker's ask mirrored onto its lead's run carries the worker's call.
    arguments = approval.get("arguments")
    for message in reversed(record.get("context", {}).get("messages", [])) if arguments is None else ():
        for call in (message.get("metadata") or {}).get("tool_calls") or []:
            if call.get("id") == approval.get("tool_call_id"):
                raw = (call.get("function") or {}).get("arguments")
                try:
                    arguments = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    arguments = raw
                break
        if arguments is not None:
            break
    return {
        "approval_id": approval["approval_id"],
        "tool_call_id": approval.get("tool_call_id"),
        "tool_name": approval.get("tool_name"),
        "capability": approval.get("capability"),
        "target": approval.get("target"),
        "arguments": arguments,
        # For a shell command: the commands it would run, one per line. The
        # target alone says only `sh` (the 0.5.0rc1 gate).
        "command": approval.get("command"),
        # For a folder operation: the files under an ask rule that this one
        # question covers, the first few and "and N more".
        "covered_files": approval.get("covered_files"),
        "risk_level": approval.get("risk_level"),
        "reason": approval.get("reason"),
        "expires_at": approval.get("expires_at"),
        "delegated_run_id": approval.get("delegated_run_id"),
        "delegated_name": approval.get("delegated_name"),
    }


def _not_resumable(record: dict[str, Any], run_id: str) -> Optional[str]:
    """Why a run cannot be continued now, or None if it can."""
    from omnicoreagent.core.runs import not_resumable

    return not_resumable(record, run_id)


def _privacy_summary(config: Any) -> Dict[str, Any]:
    """Which boundaries redact personal data, and which kinds."""
    boundaries = ("telemetry", "memory", "workspace", "stream", "public", "model_io")
    return {
        "enabled": bool(config.enabled),
        "redacted": [
            name for name in boundaries if config.enabled and getattr(config, f"redact_{name}")
        ],
        "categories": list(config.categories),
    }


def _with_approval_arguments(record: Dict[str, Any]) -> Dict[str, Any]:
    """A run record with each approval's call as the model made it: an
    approver in another process has only this record, not the run's result."""
    return {
        **record,
        # Always present, as over HTTP: a run with none read without the key
        # and a reader crashed on it (the 0.5.0rc4 gate).
        "budget_requests": list(record.get("budget_requests") or []),
        "outcomes": list(record.get("outcomes") or []),
        "approvals": [
            {**approval, "arguments": _public_approval(approval, record)["arguments"]}
            for approval in record.get("approvals") or []
        ],
    }


def _add_totals(total: Any, segment: Any) -> Any:
    """Sum run totals across segments: numbers add, lists join, dicts merge."""
    if isinstance(total, dict) and isinstance(segment, dict):
        merged = dict(total)
        for key, value in segment.items():
            merged[key] = _add_totals(total[key], value) if key in total else value
        return merged
    if isinstance(total, bool) or isinstance(segment, bool):
        return segment
    if isinstance(total, (int, float)) and isinstance(segment, (int, float)):
        return total + segment
    if isinstance(total, list) and isinstance(segment, list):
        return [*total, *segment]
    return segment if segment is not None else total


# A run in one of these is over; running, awaiting_approval and interrupted
# runs continue, and are not yet anything to learn from.
_FINISHED_RUN_STATUSES = frozenset({"completed", "blocked", "failed", "cancelled"})
# What run retention may remove: every status a run ends in, including a run
# ended from outside as timed out. Waiting and running runs are never listed.
_PRUNABLE_RUN_STATUSES = frozenset({*_FINISHED_RUN_STATUSES, "timeout", "abandoned"})
_UNFINISHED_TRACE_STATUSES = frozenset({"running", "suspended"})


def _training_record(
    segments: List[Dict[str, Any]], run: Dict[str, Any] | None = None
) -> Dict[str, Any] | None:
    """One finished run, as a trainer reads it, from every trace segment it
    took: nothing when its model calls were not recorded (the privacy-first
    capture)."""
    steps = []
    policy_version: Dict[str, Any] = {}
    totals: Dict[str, Any] = {}
    for index, trajectory in enumerate(segments):
        totals = _add_totals(totals, trajectory.get("totals") or {})
        for step in _training_steps(trajectory):
            policy_version = step.pop("_policy_version") or policy_version
            steps.append({**step, "segment": index})
    if all(step["resumed"] for step in steps):
        # No model turn was recorded (the privacy-first capture): a resumed
        # step alone holds nothing to learn from (the 0.5.0rc2 gate).
        return None
    first, last = segments[0], segments[-1]
    outcomes = (run or {}).get("outcomes")
    if outcomes is None:
        outcomes = [o for trajectory in segments for o in trajectory.get("outcomes") or []]
    return {
        "run_id": last.get("run_id"),
        "trace_id": last.get("trace_id"),
        "trace_ids": [trajectory.get("trace_id") for trajectory in segments],
        "session_id": last.get("session_id"),
        "agent": (last.get("harness") or {}).get("agent"),
        "status": last.get("status"),
        "started_at": first.get("started_at"),
        "ended_at": last.get("ended_at"),
        "policy_version": policy_version,
        "request": (first.get("request") or {}).get("message"),
        # The trajectory keeps the answer in its final event's output.
        "final_answer": ((last.get("final") or {}).get("output") or {}).get("response"),
        "outcomes": list(outcomes),
        "totals": {
            "tokens": totals.get("tokens"),
            "estimated_cost_usd": totals.get("estimated_cost_usd"),
            "duration_ms": totals.get("duration_ms"),
        },
        "evidence_status": last.get("evidence_status"),
        "steps": steps,
    }


def _training_steps(trajectory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The agent turns of one trace segment, each with the policy that served it."""
    steps = []
    for step in trajectory.get("steps") or []:
        if step.get("resumed") and not step.get("model_calls"):
            # The calls a person approved, run on resume: no model turn of
            # their own, but what they did is part of the run.
            steps.append(
                {
                    "step": step.get("step"),
                    "resumed": True,
                    "_policy_version": None,
                    "messages": None,
                    "tools": None,
                    "response": None,
                    "token_details": None,
                    "finish_reason": None,
                    "tokens": None,
                    "tool_calls": _training_tool_calls(step),
                }
            )
            continue
        for call in step.get("model_calls") or []:
            request = call.get("request") or {}
            if call.get("purpose", "agent_turn") != "agent_turn" or "messages" not in request:
                continue
            facts = call.get("facts") or {}
            response = call.get("response") or {}
            steps.append(
                {
                    "step": step.get("step"),
                    # Present on every step, so a reader can index it.
                    "resumed": False,
                    "_policy_version": facts.get("policy_version"),
                    "messages": request.get("messages"),
                    "tools": request.get("tools"),
                    "response": response,
                    "token_details": response.get("token_details"),
                    "finish_reason": facts.get("finish_reason"),
                    "tokens": facts.get("tokens"),
                    "tool_calls": _training_tool_calls(step),
                }
            )
    return steps


def _training_tool_calls(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "tool_call_id": tool.get("tool_call_id"),
            "name": tool.get("tool_name"),
            "arguments": tool.get("raw_arguments"),
            "outcome": tool.get("outcome"),
            "observation": ((tool.get("observation") or {}).get("content")),
            "error": tool.get("error"),
        }
        for tool in step.get("tool_calls") or []
    ]
