from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
import uuid

from omnicoreagent.core.runtime import (
    builder,
    construction,
    execution,
    harness_tools,
    normalization,
    summaries,
)
from omnicoreagent.core.runtime.imports import (
    LazyDefaultPromptBuilder,
    runtime,
    runtime_logger,
)
from omnicoreagent.core.telemetry import (
    ActorType,
    TelemetryActor,
    TelemetryRecorder,
    TelemetryStream,
    TelemetryStreamScope,
    TraceFilter,
    TraceStatus,
)


class OmniCoreAgent:
    """
    Public facade for the OmniCoreAgent runtime.

    The facade owns lifecycle, session, memory, event, and execution APIs while
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
        event_router: Optional[Any] = None,
        telemetry_store: Optional[Any] = None,
        telemetry_recorder: Optional[Any] = None,
        telemetry_stream: Optional[Any] = None,
        prompt_builder: Optional[Any] = None,
        debug: bool = False,
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
            event_router: Optional event router (EventRouter)
            telemetry_store: Optional telemetry store
            telemetry_recorder: Optional telemetry recorder
            telemetry_stream: Optional telemetry stream
            debug: Enable debug logging
        """
        self.name = name
        self.system_instruction = system_instruction
        self.model_config = normalization.build_model_config(model_config)
        self.mcp_tools = normalization.build_mcp_tools(mcp_tools)
        self.local_tools = normalization.normalize_local_tools(local_tools)

        self.sub_agents = sub_agents
        self.agent_config = normalization.build_agent_config(name, agent_config)

        self.debug = debug
        self._cumulative_usage = None

        self.memory_router = memory_router
        self.event_router = event_router
        self.telemetry_store = telemetry_store
        self.telemetry_recorder = telemetry_recorder
        self.telemetry_stream = telemetry_stream
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

        if not self.event_router:
            self.event_router = construction.default_event_router()

        self._ensure_telemetry()

        agent_cfg = self.agent_config
        self.guardrail_mode, self.guardrail = construction.build_guardrail(
            self.name, agent_cfg
        )

        self._create_agent()
        self._initialized = True

    def _create_agent(self):
        """Build and attach runtime components."""
        components = builder.build_agent_runtime(
            model_config=self.model_config,
            mcp_tools=self.mcp_tools,
            local_tools=self.local_tools,
            agent_config=self.agent_config,
            memory_router=self.memory_router,
            event_router=self.event_router,
            prompt_builder=self.prompt_builder,
            existing_subagent_factory=self._subagent_factory,
            guardrail=self.guardrail,
            guardrail_mode=self.guardrail_mode,
            summarize_fn=self._summarize_history,
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
            runtime_logger().warning(
                "No LLM connection available for summarization"
            )
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
        """Attach default telemetry components without touching legacy events."""
        if self.telemetry_store is None:
            if self.telemetry_recorder is not None:
                self.telemetry_store = self.telemetry_recorder.store
            elif self.telemetry_stream is not None:
                self.telemetry_store = self.telemetry_stream.store
            else:
                self.telemetry_store = construction.default_telemetry_store()

        if self.telemetry_recorder is None:
            self.telemetry_recorder = TelemetryRecorder(self.telemetry_store)
        elif self.telemetry_recorder.store is not self.telemetry_store:
            raise ValueError(
                "telemetry_recorder.store must be the same object as telemetry_store"
            )

        if self.telemetry_stream is None:
            self.telemetry_stream = TelemetryStream(self.telemetry_store)
        elif self.telemetry_stream.store is not self.telemetry_store:
            raise ValueError(
                "telemetry_stream.store must be the same object as telemetry_store"
            )

    def _telemetry_actor(self) -> TelemetryActor:
        return TelemetryActor(type=ActorType.AGENT, name=self.name)

    def _telemetry_metadata(self) -> dict[str, Any]:
        return {
            "agent_name": self.name,
            "model_provider": self.model_config.get("provider"),
            "model": self.model_config.get("model"),
        }

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
            harness_tools.index_tools_for_advanced_use(
                enabled=self.agent.enable_advanced_tool_use,
                mcp_tools=self.mcp_client.available_tools if self.mcp_client else {},
                local_tools=self.local_tools,
            )

    async def run(
        self,
        query: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the agent with a query and optional session ID.

        Args:
            query: The user query
            session_id: Optional session ID for session continuity
            run_id: Optional run ID for serving/event/telemetry correlation

        Returns:
            Dict containing response, session_id, trace_id, and run_id.
        """
        if not session_id:
            session_id = self.generate_session_id()

        self._ensure_telemetry()

        from omnicoreagent.core.events.base import (
            current_event_run_id,
            reset_event_run_id,
            set_event_run_id,
        )

        run_id = run_id or current_event_run_id() or self.generate_run_id()
        event_run_token = set_event_run_id(run_id)
        trace_context = None
        try:
            trace_context = await self.telemetry_recorder.start_trace(
                name="agent.run",
                kind="agent.run",
                actor=self._telemetry_actor(),
                run_id=run_id,
                session_id=session_id,
                agent_id=self.name,
                metadata=self._telemetry_metadata(),
                input={"query": query},
            )
            await self.telemetry_recorder.emit_event(
                "user_message",
                actor=TelemetryActor(type=ActorType.USER),
                input={"message": query},
            )

            if not self._initialized:
                await self.initialize()

            blocked_response = execution.blocked_guardrail_response(
                guardrail=self.guardrail,
                query=query,
                session_id=session_id,
                agent_name=self.name,
            )
            if blocked_response:
                await self.telemetry_recorder.emit_event(
                    "guardrail_violation",
                    actor=TelemetryActor(type=ActorType.GUARDRAIL, name=self.name),
                    input={"query": query},
                    output=blocked_response.get("guardrail_result"),
                )
                await self.telemetry_recorder.emit_event(
                    "final_answer",
                    actor=self._telemetry_actor(),
                    output={"response": blocked_response["response"]},
                )
                await self.telemetry_recorder.end_trace(
                    status=TraceStatus.ABORTED_SAFETY_GUARD,
                    output={"response": blocked_response["response"]},
                )
                blocked_response["trace_id"] = trace_context.trace_id
                blocked_response["run_id"] = run_id
                return blocked_response

            runtime_prompt = self.prompt_builder.build(
                system_instruction=self.system_instruction
            )

            response = await self.agent.run(
                system_prompt=runtime_prompt,
                query=query,
                llm_connection=self.llm_connection,
                add_message_to_history=self.memory_router.store_message,
                message_history=self.memory_router.get_messages,
                debug=self.debug,
                event_router=self.event_router.append,
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
            await self.telemetry_recorder.emit_event(
                "final_answer",
                actor=self._telemetry_actor(),
                output={"response": formatted_response.get("response")},
            )
            await self.telemetry_recorder.end_trace(
                status=TraceStatus.COMPLETED,
                output={"response": formatted_response.get("response")},
            )
            formatted_response["trace_id"] = trace_context.trace_id
            formatted_response["run_id"] = run_id
            return formatted_response
        except asyncio.CancelledError as exc:
            if trace_context is not None:
                await self.telemetry_recorder.emit_event(
                    "final_state",
                    actor=self._telemetry_actor(),
                    output={"status": TraceStatus.CANCELLED.value},
                )
                await self.telemetry_recorder.end_trace(
                    status=TraceStatus.CANCELLED,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
            raise
        except Exception as exc:
            if trace_context is not None:
                await self.telemetry_recorder.record_exception(
                    exc,
                    event_type="runtime_error",
                    actor=self._telemetry_actor(),
                    metadata={"phase": "agent.run"},
                )
                await self.telemetry_recorder.end_trace(
                    status=TraceStatus.FAILED,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
            raise
        finally:
            reset_event_run_id(event_run_token)

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

    async def list_all_available_tools(self):
        """List all available tools (MCP and local)"""
        if not self._initialized:
            await self.initialize()

        return harness_tools.available_tools(self.mcp_client, self.local_tools)

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

    async def stream_events(self, session_id: str):
        async for event in self.event_router.stream(session_id=session_id):
            yield event

    async def get_event_stream_cursor(self, session_id: str) -> str | None:
        return await self.event_router.get_stream_cursor(session_id=session_id)

    async def stream_events_after(self, session_id: str, cursor: str | None):
        async for event in self.event_router.stream_after(
            session_id=session_id,
            cursor=cursor,
        ):
            yield event

    async def get_events_after(self, session_id: str, cursor: str | None):
        return await self.event_router.get_events_after(
            session_id=session_id,
            cursor=cursor,
        )

    async def get_events(self, session_id: str):
        return await self.event_router.get_events(session_id=session_id)

    async def get_event_trace(self, session_id: str) -> Dict[str, Any]:
        trace = await self.event_router.get_trace(session_id=session_id)
        return trace.model_dump()

    async def get_trace(
        self,
        identifier: str | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> Dict[str, Any] | None:
        """
        Return a telemetry trace by trace_id, or a legacy event summary by session_id.

        Passing a returned ``run()`` trace_id uses telemetry. Passing session_id keeps
        the legacy event-summary behavior until EventRouter migration is complete.
        """
        if trace_id is not None:
            return await self.get_telemetry_trace(trace_id)
        if session_id is not None:
            return await self.get_event_trace(session_id)
        if identifier is None:
            raise TypeError("get_trace() requires trace_id or session_id")
        if identifier.startswith("trace_"):
            return await self.get_telemetry_trace(identifier)
        return await self.get_event_trace(identifier)

    async def get_telemetry_trace(self, trace_id: str) -> Dict[str, Any] | None:
        self._ensure_telemetry()
        trace = await self.telemetry_store.get_trace(trace_id)
        return trace.model_dump() if trace else None

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
        return [trace.model_dump() for trace in traces]

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

    async def get_event_store_type(self) -> str:
        """Get the current event store type."""
        return self.event_router.get_event_store_type()

    async def is_event_store_available(self) -> bool:
        """Check if the event store is available."""
        return self.event_router.is_available()

    async def get_event_store_info(self) -> Dict[str, Any]:
        """Get information about the current event store."""
        return self.event_router.get_event_store_info()

    async def switch_event_store(self, event_store_type: str):
        """Switch to a different event store type."""
        self.event_router.switch_event_store(event_store_type)

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
