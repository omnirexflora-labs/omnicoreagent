import asyncio
import time

import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Tuple
from omnicoreagent.core.system_prompts import (
    FAST_CONVERSATION_SUMMARY_PROMPT,
)
from omnicoreagent.core.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.types import (
    AgentState,
    Message,
    ParsedResponse,
    ToolCallResult,
    ToolError,
    SessionState,
)
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.tool_batch_runner import ToolBatchRunner
from omnicoreagent.core.tools.tool_call_resolver import ToolCallResolver
from omnicoreagent.core.tools.tool_failure_handler import ToolFailureHandler
from omnicoreagent.core.tools.tool_runtime_registry import ToolRuntimeRegistry
from omnicoreagent.core.agents.prompt_context import AgentPromptContextBuilder
from omnicoreagent.core.utils import (
    RobustLoopDetector,
    logger,
    show_tool_response,
    track,
    BackgroundTaskManager,
    resolve_agent,
    build_kwargs,
    build_sub_agents_observation_xml,
    show_sub_agent_call_result,
)
from datetime import datetime
from omnicoreagent.core.events.base import (
    Event,
    EventType,
    FinalAnswerPayload,
    AgentMessagePayload,
    UserMessagePayload,
    AgentThoughtPayload,
    SubAgentCallStartedPayload,
    SubAgentCallResultPayload,
    SubAgentCallErrorPayload,
)
from omnicoreagent.core.context_manager import (
    AgentLoopContextManager,
    ContextManagementConfig,
)
from omnicoreagent.core.tool_response_offloader import (
    ToolResponseOffloader,
    OffloadConfig,
)
from omnicoreagent.core.tools.tool_observation import ToolObservationHandler
from omnicoreagent.core.guardrails import PromptInjectionGuard
from omnicoreagent.core.agents.xml_parser import (
    extract_thought,
    parse_action_or_answer,
)

class BaseReactAgent:
    """Autonomous agent implementing the ReAct paradigm for task solving through iterative reasoning and tool usage."""

    def __init__(
        self,
        agent_name: str,
        max_steps: int,
        tool_call_timeout: int,
        request_limit: int = 0,
        total_tokens_limit: int = 0,
        enable_advanced_tool_use: bool = False,
        enable_subagents: bool = False,
        enable_workspace_memory: bool = False,
        enable_agent_skills: bool = False,
        context_management_config: dict = None,
        tool_offload_config: dict = None,
        guardrail: PromptInjectionGuard | None = None,
    ):
        self.agent_name = agent_name
        self.max_steps = max(max_steps, 5)
        if max_steps < 5:
            logger.warning(
                f"Agent {agent_name}: max_steps increased from {max_steps} to 5 (minimum required for tool usage)"
            )
        self.tool_call_timeout = tool_call_timeout

        self.request_limit = request_limit
        self.total_tokens_limit = total_tokens_limit
        self._limits_enabled = request_limit > 0 or total_tokens_limit > 0
        self.enable_advanced_tool_use = enable_advanced_tool_use
        self.enable_subagents = enable_subagents

        self.enable_workspace_memory = enable_workspace_memory
        self.enable_agent_skills = enable_agent_skills
        self.skill_manager = None
        self.usage_limits = UsageLimits(
            request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit
        )

        self._session_states: dict[Tuple[str, str], SessionState] = {}
        self.background_task_manager = BackgroundTaskManager()
        self.init_skills()
        self.register_internal_tool = ToolRegistry()

        self.context_manager = AgentLoopContextManager(
            ContextManagementConfig.from_dict(context_management_config or {})
        )

        self.tool_offloader = ToolResponseOffloader(
            config=OffloadConfig.from_dict(tool_offload_config or {})
        )
        self.guardrail = guardrail
        self.tool_observation_handler = ToolObservationHandler(
            agent_name=self.agent_name,
            tool_offloader=self.tool_offloader,
            guardrail=self.guardrail,
        )
        self.tool_call_resolver = ToolCallResolver(guardrail=self.guardrail)
        self.tool_failure_handler = ToolFailureHandler(agent_name=self.agent_name)
        self.tool_batch_runner = ToolBatchRunner(
            agent_name=self.agent_name,
            tool_call_timeout=self.tool_call_timeout,
        )
        self.tool_runtime_registry = ToolRuntimeRegistry(
            register_internal_tool=self.register_internal_tool,
            tool_offloader=self.tool_offloader,
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_memory=self.enable_workspace_memory,
            enable_agent_skills=self.enable_agent_skills,
            skill_manager=self.skill_manager,
        )
        self.prompt_context_builder = AgentPromptContextBuilder(
            enable_advanced_tool_use=self.enable_advanced_tool_use,
            enable_subagents=self.enable_subagents,
            enable_workspace_memory=self.enable_workspace_memory,
            enable_agent_skills=self.enable_agent_skills,
            is_tool_offload_enabled=lambda: self.tool_offloader.config.enabled,
            skill_manager=self.skill_manager,
        )

    def init_skills(self):
        if self.enable_agent_skills:
            from omnicoreagent.core.skills.manager import SkillManager

            self.skill_manager = SkillManager()
            self.skill_manager.discover_skills()
            logger.info(
                f"Agent Skills enabled: found {len(self.skill_manager.skills)} skills"
            )

    def _get_session_state(self, session_id: str, debug: bool) -> SessionState:
        key = (session_id, self.agent_name)
        if key not in self._session_states:
            self._session_states[key] = SessionState(
                messages=[],
                state=AgentState.IDLE,
                loop_detector=RobustLoopDetector(debug=debug),
                assistant_with_tool_calls=None,
                pending_tool_responses=[],
            )
        return self._session_states[key]

    async def extract_action_or_answer(
        self,
        response: str,
        session_id: str,
        event_router: Callable,
        debug: bool = False,
    ) -> ParsedResponse:
        """Parse LLM response to extract a final answer, tool call, or agent call using XML format only."""
        thought = extract_thought(response)
        if thought:
            event = Event(
                type=EventType.AGENT_THOUGHT,
                payload=AgentThoughtPayload(message=thought),
                agent_name=self.agent_name,
            )
            if event_router:
                await event_router(session_id=session_id, event=event)

        return parse_action_or_answer(response, debug=debug)

    @track("memory_processing")
    async def update_llm_working_memory(
        self,
        message_history: Callable[[], Any],
        session_id: str,
        llm_connection: Callable,
        debug: bool,
    ):
        """Update the LLM's working memory with the current message history and process memory asynchronously"""

        short_term_memory_message_history = await message_history(
            agent_name=self.agent_name, session_id=session_id
        )
        if not short_term_memory_message_history:
            return

        validated_messages = [
            Message.model_validate(msg) if isinstance(msg, dict) else msg
            for msg in short_term_memory_message_history
        ]
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        for message in validated_messages:
            role = message.role
            metadata = message.metadata

            if role == "user":
                if not message.content.strip().startswith("<observations>"):
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.messages.append(
                        Message(role="user", content=message.content)
                    )

            elif role == "assistant":
                if metadata.has_tool_calls:
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.assistant_with_tool_calls = {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": (
                            [tc.model_dump() for tc in metadata.tool_calls]
                            if metadata.tool_calls
                            else []
                        ),
                    }
                    session_state.pending_tool_responses = []
                else:
                    self._try_flush_pending(session_id=session_id, debug=debug)
                    session_state.messages.append(
                        Message(role="assistant", content=message.content)
                    )

            elif role == "tool":
                session_state.pending_tool_responses.append(
                    {
                        "role": "tool",
                        "content": message.content,
                        "tool_call_id": metadata.tool_call_id,
                    }
                )
                self._try_flush_pending(session_id=session_id, debug=debug)

            else:
                logger.warning(f"Unknown message role encountered: {role}")

    def _try_flush_pending(self, session_id: str, debug: bool):
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        if session_state.assistant_with_tool_calls:
            expected = {
                tc["id"]
                for tc in session_state.assistant_with_tool_calls.get("tool_calls", [])
            }
            actual = {
                resp["tool_call_id"] for resp in session_state.pending_tool_responses
            }
            if not (expected - actual):
                session_state.messages.append(session_state.assistant_with_tool_calls)
                session_state.messages.extend(session_state.pending_tool_responses)
                session_state.assistant_with_tool_calls = None
                session_state.pending_tool_responses = []

    async def resolve_tool_call_request(
        self,
        parsed_response: ParsedResponse,
        sessions: dict,
        mcp_tools: dict,
        local_tools: Any = None,
        sub_agents: list = None,
    ) -> ToolError | list[ToolCallResult]:
        return await self.tool_call_resolver.resolve(
            parsed_response=parsed_response,
            sessions=sessions,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

    @track("tool_execution")
    async def act(
        self,
        parsed_response: ParsedResponse,
        response: str,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        system_prompt: str,
        debug: bool = False,
        sessions: dict = None,
        mcp_tools: dict = None,
        local_tools: Any = None,
        session_id: str = None,
        event_router: Callable[[str, Event], Any] = None,
        sub_agents: list = None,
    ):
        session_state = self._get_session_state(session_id=session_id, debug=debug)

        tool_call_result = await self.resolve_tool_call_request(
            parsed_response=parsed_response,
            mcp_tools=mcp_tools,
            sessions=sessions,
            local_tools=local_tools,
            sub_agents=sub_agents,
        )

        tools_results = []
        obs_text = None

        if isinstance(tool_call_result, ToolError):
            (
                tool_batch_name,
                tool_batch_args,
                obs_text,
                tools_results,
            ) = await self.tool_failure_handler.handle_validation_error(
                tool_error=tool_call_result,
                session_state=session_state,
                session_id=session_id,
                event_router=event_router,
            )
        else:
            tool_call_result = list(tool_call_result)
            tool_batch_name, tool_batch_args = await self.tool_batch_runner.start(
                tool_call_results=tool_call_result,
                response=response,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                event_router=event_router,
            )
            obs_text, tools_results = await self.tool_batch_runner.execute(
                tool_call_results=tool_call_result,
                session_state=session_state,
                add_message_to_history=add_message_to_history,
                session_id=session_id,
                event_router=event_router,
                tool_batch_name=tool_batch_name,
                tool_batch_args=tool_batch_args,
                parse_tool_observation=self.tool_observation_handler.parse,
                build_tool_results_observation=(
                    self.tool_observation_handler.build_results_observation
                ),
            )

        if debug:
            show_tool_response(
                agent_name=self.agent_name,
                tool_name=tool_batch_name,
                tool_args=tool_batch_args,
                observation=obs_text,
            )

        await self.tool_observation_handler.append_observations(
            tools_results=tools_results,
            session_state=session_state,
            add_message_to_history=add_message_to_history,
            session_id=session_id,
            debug=debug,
        )

        if isinstance(tool_call_result, (list, tuple)):
            tool_call_results = list(tool_call_result)
        else:
            tool_call_results = [tool_call_result]

        await self.tool_failure_handler.handle_loop_state(
            tool_call_results=tool_call_results,
            session_state=session_state,
            system_prompt=system_prompt,
            session_id=session_id,
            event_router=event_router,
            debug=debug,
            reset_system_prompt=self.reset_system_prompt,
        )

    async def reset_system_prompt(self, messages: list, system_prompt: str):
        old_messages = messages[1:]
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(old_messages)
        return messages

    @asynccontextmanager
    async def agent_session_state_context(
        self, new_state: AgentState, session_id: str, debug: bool
    ):
        """Context manager to change the agent session state"""
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid agent state: {new_state}")
        previous_state = session_state.state
        session_state.state = new_state
        try:
            yield
        except Exception as e:
            session_state.state = AgentState.ERROR
            logger.error(f"Error in agent state context: {e}")
            raise
        finally:
            session_state.state = previous_state

    async def prepare_initial_messages(
        self,
        session_state,
        system_prompt: str,
        session_id: str,
        llm_connection: Callable,
        message_history: Callable[[], Any],
        mcp_tools: dict = None,
        local_tools: Any = None,
        debug: bool = False,
        sub_agents: list = None,
    ) -> None:
        """
        Prepare the full initial message list for the LLM by concurrently:
        - Building tool registry
        - Loading prior message history
        - Injecting current user query
        """
        tasks = {}

        tasks["tools"] = self.tool_runtime_registry.render_prompt_registry(
            mcp_tools=mcp_tools, local_tools=local_tools
        )

        tasks["history"] = self.update_llm_working_memory(
            message_history=message_history,
            session_id=session_id,
            llm_connection=llm_connection,
            debug=debug,
        )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    tasks["tools"],
                    tasks["history"],
                    return_exceptions=True,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout during initial message preparation (20s). Proceeding with defaults."
            )
            results = ["No tools available", None]

        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"prepare_initial_messages error: {r}", exc_info=True)

        tools_section = (
            results[0]
            if not isinstance(results[0], BaseException)
            else "No tools available"
        )

        updated_system_prompt = await self.prompt_context_builder.build_system_prompt(
            base_system_prompt=system_prompt,
            tools_section=tools_section,
            sub_agents=sub_agents,
        )

        session_state.messages.insert(
            0, Message(role="system", content=updated_system_prompt)
        )
        self.prompt_context_builder.inject_current_datetime(session_state.messages)

    async def execute_sub_agent_calls(
        self,
        response: str,
        agent_calls: list,
        sub_agents: list,
        session_id: str,
        session_state: Any,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        run_usage: Usage,
        event_router: Callable[[str, Event], Any] = None,
        debug: bool = False,
    ):
        """
        Execute multiple sub-agent calls in parallel with proper observation formatting.

        This function:
        1. Connects all MCP servers concurrently (if needed)
        2. Executes all sub-agent runs concurrently
        3. Formats results into proper XML observations
        4. Adds observations to message history
        """
        event = Event(
            type=EventType.SUB_AGENT_CALL_STARTED,
            payload=SubAgentCallStartedPayload(
                agent_name=self.agent_name,
                session_id=session_id,
                timestamp=str(datetime.now()),
                run_count=0,
                kwargs={"agent_calls": agent_calls},
            ),
            agent_name=self.agent_name,
        )
        if event_router:
            await event_router(session_id=session_id, event=event)
        metadata = {"agent_calls": agent_calls}
        await add_message_to_history(
            role="assistant",
            content=response,
            metadata=metadata,
            session_id=session_id,
        )
        session_state.messages.append(Message(role="assistant", content=response))

        if isinstance(agent_calls, str):
            agent_calls = json.loads(agent_calls)

        async def execute_single_agent(call: dict) -> tuple[str, Any]:
            """Execute a single agent, handling MCP connection if needed."""
            agent_name = call.get("agent")
            if not agent_name:
                raise ValueError("agent_call missing 'agent' field")

            try:
                agent = resolve_agent(agent_name, sub_agents)
                params = call.get("parameters", {})
                params["session_id"] = session_id
                kwargs = build_kwargs(agent, params)

                if hasattr(agent, "mcp_tools") and agent.mcp_tools:
                    logger.info(f"Connecting MCP servers for {agent_name}...")
                    await agent.connect_mcp_servers()

                logger.info(f"Running sub-agent: {agent_name}")
                result = await agent.run(**kwargs)
                await agent.cleanup_mcp_servers()
                return agent_name, result

            except Exception as e:
                logger.error(f"Error executing agent {agent_name}: {e}", exc_info=True)
                return agent_name, e

        logger.info(
            f"Executing {len(agent_calls)} sub-agents with concurrent MCP connections..."
        )
        tasks = [execute_single_agent(call) for call in agent_calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        observations = []

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Unexpected top-level exception: {result}")
                obs = {
                    "agent_name": "unknown",
                    "status": "error",
                    "output": str(result),
                }
                observations.append(obs)
            else:
                agent_name, obs_data = result

                if isinstance(obs_data, Exception):
                    logger.error(f"Agent {agent_name} execution failed: {obs_data}")
                    obs = {
                        "agent_name": agent_name,
                        "status": "error",
                        "output": str(obs_data),
                    }
                    observations.append(obs)
                    event = Event(
                        type=EventType.SUB_AGENT_CALL_ERROR,
                        payload=SubAgentCallErrorPayload(
                            agent_name=agent_name,
                            session_id=session_id,
                            timestamp=str(datetime.now()),
                            error=str(obs_data),
                            error_count=0,
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        await event_router(session_id=session_id, event=event)
                else:
                    if isinstance(obs_data, dict):
                        agent_response = obs_data.get(
                            "response", obs_data.get("output", str(obs_data))
                        )
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": agent_response,
                        }
                    elif isinstance(obs_data, str):
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": obs_data,
                        }
                    else:
                        obs = {
                            "agent_name": agent_name,
                            "status": "success",
                            "output": str(obs_data),
                        }

                    logger.info(f"Agent {agent_name} completed successfully")
                    observations.append(obs)
                    if isinstance(obs_data, dict):
                        sub_usage = obs_data.get("metric")
                        if sub_usage and isinstance(sub_usage, Usage):
                            run_usage.incr(sub_usage)
                            usage.incr(sub_usage)

                    event = Event(
                        type=EventType.SUB_AGENT_CALL_RESULT,
                        payload=SubAgentCallResultPayload(
                            agent_name=agent_name,
                            session_id=session_id,
                            timestamp=str(datetime.now()),
                            run_count=0,
                            result=obs_data,
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        await event_router(session_id=session_id, event=event)

        xml_obs_block = build_sub_agents_observation_xml(observations)
        agent_call_result = {
            "agent_name": self.agent_name,
            "agent_calls": agent_calls,
            "output": observations,
        }

        if debug:
            show_sub_agent_call_result(agent_call_result)

        session_state.messages.append(Message(role="user", content=xml_obs_block))
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name, "sub_agent_results": True},
        )

    @track("agent_execution")
    async def run(
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
        event_router: Callable[[str, Event], Any] = None,
        sub_agents: list = None,
    ) -> Any:
        """Execute ReAct loop with JSON communication
        kwargs: if mcp is enbale then it will be sessions and availables_tools else it will be local_tools
        """
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        session_state.messages = []
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        session_state.loop_detector.reset()
        start_time = time.perf_counter()
        run_usage = Usage()

        event = Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(
                message=query,
            ),
            agent_name=self.agent_name,
        )
        if event_router:
            await event_router(session_id=session_id, event=event)

        await add_message_to_history(
            role="user",
            content=query,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )
        runtime_local_tools = await self.tool_runtime_registry.prepare_tools(
            local_tools=local_tools
        )
        await self.prepare_initial_messages(
            system_prompt=system_prompt,
            session_state=session_state,
            llm_connection=llm_connection,
            message_history=message_history,
            mcp_tools=mcp_tools,
            local_tools=runtime_local_tools,
            session_id=session_id,
            debug=debug,
            sub_agents=sub_agents,
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
            last_valid_response = None
            while (
                session_state.state not in [AgentState.FINISHED]
                and current_steps < self.max_steps
            ):
                if debug:
                    logger.info(
                        f"Sending {len(session_state.messages)} messages to LLM"
                    )
                current_steps += 1
                if self._limits_enabled:
                    self.usage_limits.check_before_request(usage=usage)

                try:
                    if self.context_manager.should_trigger(session_state.messages):

                        async def _summarize_for_context(msgs):
                            """Summarize messages for context management."""
                            history_text = "\n".join(
                                [
                                    f"{m.role if hasattr(m, 'role') else m.get('role', 'unknown')}: "
                                    f"{m.content if hasattr(m, 'content') else m.get('content', '')}"
                                    for m in msgs
                                ]
                            )
                            summary_msgs = [
                                {
                                    "role": "system",
                                    "content": FAST_CONVERSATION_SUMMARY_PROMPT,
                                },
                                {
                                    "role": "user",
                                    "content": f"Here is the conversation history: {history_text}",
                                },
                            ]
                            response = await llm_connection.llm_call(summary_msgs)
                            if hasattr(response, "choices") and response.choices:
                                response = response.choices[0].message.content.strip()
                            elif hasattr(response, "message"):
                                response = response.message.content.strip()
                            elif hasattr(response, "text"):
                                response = response.text.strip()
                            elif hasattr(response, "content"):
                                response = response.content.strip()
                            elif isinstance(response, dict) and "choices" in response:
                                response = response["choices"][0]["message"][
                                    "content"
                                ].strip()
                            elif isinstance(response, str):
                                pass
                            else:
                                response = ""

                            return response

                        session_state.messages = (
                            await self.context_manager.manage_context(
                                messages=session_state.messages,
                                summarize_fn=_summarize_for_context,
                            )
                        )
                        if debug:
                            logger.info(
                                f"Context managed: now {len(session_state.messages)} messages"
                            )

                    @track("llm_call")
                    async def make_llm_call():
                        return await llm_connection.llm_call(session_state.messages)

                    response = await make_llm_call()

                    if response:
                        # Extract the actual message content from the response
                        if hasattr(response, "choices") and response.choices:
                            message_content = response.choices[0].message.content
                        elif hasattr(response, "content"):
                            message_content = response.content
                        else:
                            message_content = str(response)

                        event = Event(
                            type=EventType.AGENT_MESSAGE,
                            payload=AgentMessagePayload(
                                message=message_content,
                            ),
                            agent_name=self.agent_name,
                        )
                        if event_router:
                            # Await to ensure event is queued before continuing
                            await event_router(session_id=session_id, event=event)

                        if hasattr(response, "usage"):
                            request_usage = Usage(
                                requests=1,
                                request_tokens=response.usage.prompt_tokens,
                                response_tokens=response.usage.completion_tokens,
                                total_tokens=response.usage.total_tokens,
                            )
                            usage.incr(request_usage)
                            run_usage.incr(request_usage)

                            if self._limits_enabled:
                                self.usage_limits.check_tokens(usage)
                                remaining_tokens = self.usage_limits.remaining_tokens(
                                    usage
                                )
                                used_tokens = usage.total_tokens
                                used_requests = usage.requests
                                remaining_requests = self.request_limit - used_requests
                                session_stats.update(
                                    {
                                        "used_requests": used_requests,
                                        "used_tokens": used_tokens,
                                        "remaining_requests": remaining_requests,
                                        "remaining_tokens": remaining_tokens,
                                        "request_tokens": request_usage.request_tokens,
                                        "response_tokens": request_usage.response_tokens,
                                        "total_tokens": request_usage.total_tokens,
                                    }
                                )
                                if debug:
                                    logger.info(
                                        f"API Call Stats - Requests: {used_requests}/{self.request_limit}, "
                                        f"Tokens: {used_tokens}/{self.usage_limits.total_tokens_limit}, "
                                        f"Request Tokens: {request_usage.request_tokens}, "
                                        f"Response Tokens: {request_usage.response_tokens}, "
                                        f"Total Tokens: {request_usage.total_tokens}, "
                                        f"Remaining Requests: {remaining_requests}, "
                                        f"Remaining Tokens: {remaining_tokens}"
                                    )
                        if hasattr(response, "choices") and response.choices:
                            response = response.choices[0].message.content.strip()
                        elif hasattr(response, "message"):
                            response = response.message.content.strip()
                        elif hasattr(response, "text"):
                            response = response.text.strip()
                        elif hasattr(response, "content"):
                            response = response.content.strip()
                        elif isinstance(response, dict) and "choices" in response:
                            response = response["choices"][0]["message"][
                                "content"
                            ].strip()
                        elif isinstance(response, str):
                            pass
                        else:
                            raise Exception(
                                f"No valid response content found in LLM response: {type(response)}"
                            )
                except UsageLimitExceeded as e:
                    error_message = f"Usage limit error: {e}"
                    logger.error(error_message)
                    return {"answer": error_message, "usage": run_usage}

                except Exception as e:
                    error_message = "Model encountered an error, please do retry again"
                    logger.error(f"{error_message}: {e}")
                    return {"answer": error_message, "usage": run_usage}

                parsed_response = await self.extract_action_or_answer(
                    response=response,
                    debug=debug,
                    session_id=session_id,
                    event_router=event_router,
                )
                if debug:
                    logger.info(f"current steps: {current_steps}")
                if parsed_response.answer is not None:
                    last_valid_response = parsed_response.answer

                    session_state.messages.append(
                        Message(
                            role="assistant",
                            content=parsed_response.answer,
                        )
                    )

                    event = Event(
                        type=EventType.FINAL_ANSWER,
                        payload=FinalAnswerPayload(
                            message=str(parsed_response.answer),
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        # CRITICAL: Await the event emission to ensure it's in the queue
                        # before run() returns. This prevents race conditions with SSE streaming.
                        await event_router(session_id=session_id, event=event)
                    await add_message_to_history(
                        role="assistant",
                        content=parsed_response.answer,
                        session_id=session_id,
                        metadata={"agent_name": self.agent_name},
                    )

                    session_state.state = AgentState.FINISHED
                    run_usage.total_time = time.perf_counter() - start_time
                    return {"answer": parsed_response.answer, "usage": run_usage}

                if parsed_response.action is not None:
                    if parsed_response.agent_calls is not None:
                        agent_calls = parsed_response.data

                        @track("sub_agent_action_execution")
                        async def execute_sub_agent_calls():
                            await self.execute_sub_agent_calls(
                                response=response,
                                agent_calls=agent_calls,
                                sub_agents=sub_agents,
                                session_id=session_id,
                                session_state=session_state,
                                add_message_to_history=add_message_to_history,
                                run_usage=run_usage,
                                event_router=event_router,
                                debug=debug,
                            )

                        await execute_sub_agent_calls()
                    else:

                        @track("action_execution")
                        async def execute_action():
                            await self.act(
                                parsed_response=parsed_response,
                                response=response,
                                add_message_to_history=add_message_to_history,
                                system_prompt=system_prompt,
                                mcp_tools=mcp_tools,
                                debug=debug,
                                sessions=sessions,
                                local_tools=runtime_local_tools,
                                session_id=session_id,
                                event_router=event_router,
                                sub_agents=sub_agents,
                            )

                        await execute_action()

                if parsed_response.error is not None:
                    session_state.messages.append(
                        Message(
                            role="user",
                            content=parsed_response.error,
                        )
                    )
                    continue
                if current_steps >= self.max_steps:
                    session_state.state = AgentState.STUCK
                    if last_valid_response:
                        max_steps_context = f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit]\n\n"
                        return {
                            "answer": max_steps_context + last_valid_response,
                            "usage": run_usage,
                        }

                    else:
                        return {
                            "answer": f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit without valid response]",
                            "usage": run_usage,
                        }

        if session_state.state == AgentState.STUCK and last_valid_response:
            loop_context = (
                "[SYSTEM_CONTEXT: LOOP_DETECTED - Agent stuck in tool call loop]\n\n"
            )
            return loop_context + last_valid_response

        run_usage.total_time = time.perf_counter() - start_time
        return {"answer": last_valid_response, "usage": run_usage}
