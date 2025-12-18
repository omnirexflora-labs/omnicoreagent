import asyncio
import json
import re
import uuid
from collections.abc import Callable
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Tuple
from omnicoreagent.core.system_prompts import (
    tools_retriever_additional_prompt,
    memory_tool_additional_prompt,
)
from omnicoreagent.core.agents.token_usage import (
    Usage,
    UsageLimitExceeded,
    UsageLimits,
    session_stats,
    usage,
)
from omnicoreagent.core.tools.tools_handler import (
    LocalToolHandler,
    MCPToolHandler,
    ToolExecutor,
)
from omnicoreagent.core.agents.types import (
    AgentState,
    Message,
    ParsedResponse,
    ToolCall,
    ToolCallMetadata,
    ToolCallResult,
    ToolError,
    ToolFunction,
    SessionState,
)
from omnicoreagent.core.utils import (
    RobustLoopDetector,
    handle_stuck_state,
    logger,
    show_tool_response,
    track,
    is_vector_db_enabled,
    normalize_tool_args,
    build_xml_observations_block,
    BackgroundTaskManager,
)
from omnicoreagent.core.events.base import (
    Event,
    EventType,
    ToolCallErrorPayload,
    ToolCallStartedPayload,
    ToolCallResultPayload,
    FinalAnswerPayload,
    AgentMessagePayload,
    UserMessagePayload,
    AgentThoughtPayload,
)
import traceback
from omnicoreagent.core.tools.tool_knowledge_base import (
    tools_retriever_local_tool,
)
from omnicoreagent.core.tools.memory_tool.memory_tool import (
    build_tool_registry_memory_tool,
)
from omnicoreagent.core.constants import date_time_func


if is_vector_db_enabled():
    logger.info("Vector database is enabled")
    try:
        from omnicoreagent.core.memory_store.memory_management.memory_manager import (
            MemoryManagerFactory,
        )
        from omnicoreagent.core.memory_store.memory_management.background_memory_management import (
            fire_and_forget_memory_processing,
        )
    except Exception as e:
        logger.warning(f"Failed to import memory manager: {e}")
        pass
else:
    logger.info("Vector database is disabled")


class BaseReactAgent:
    """Autonomous agent implementing the ReAct paradigm for task solving through iterative reasoning and tool usage."""

    def __init__(
        self,
        agent_name: str,
        max_steps: int,
        tool_call_timeout: int,
        request_limit: int = 0,
        total_tokens_limit: int = 0,
        memory_results_limit: int = 5,
        memory_similarity_threshold: float = 0.5,
        enable_tools_knowledge_base: bool = False,
        tools_results_limit: int = 10,
        tools_similarity_threshold: float = 0.5,
        memory_tool_backend: str = None,
    ):
        self.agent_name = agent_name
        # Enforce minimum 5 steps to allow proper tool usage and reasoning
        self.max_steps = max(max_steps, 5)
        if max_steps < 5:
            logger.warning(
                f"Agent {agent_name}: max_steps increased from {max_steps} to 5 (minimum required for tool usage)"
            )
        self.tool_call_timeout = tool_call_timeout

        # Production-ready limits: 0 means unlimited (skip checks)
        self.request_limit = request_limit
        self.total_tokens_limit = total_tokens_limit
        self._limits_enabled = request_limit > 0 or total_tokens_limit > 0
        self.enable_tools_knowledge_base = enable_tools_knowledge_base

        if self._limits_enabled:
            logger.info(
                f"Usage limits enabled: {request_limit} requests, {total_tokens_limit} tokens"
            )
        else:
            logger.info("Usage limits disabled (production mode)")

        # Memory retrieval configuration with sensible defaults
        self.memory_results_limit = memory_results_limit
        self.memory_similarity_threshold = memory_similarity_threshold

        self.tools_results_limit = tools_results_limit
        self.tools_similarity_threshold = tools_similarity_threshold

        self.memory_tool_backend = memory_tool_backend
        self.usage_limits = UsageLimits(
            request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit
        )

        self._session_states: dict[Tuple[str, str], SessionState] = {}
        self.background_task_manager = BackgroundTaskManager()

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

    @track("memory_retrieval")
    async def get_long_episodic_memory(
        self,
        query: str,
        session_id: str,
        llm_connection: Callable = None,
    ):
        """Get long-term and episodic memory for a given query and session ID using optimized single query

        Args:
            query: The search query for memory retrieval
            session_id: The session ID to search within
            llm_connection: LLM connection for vector operations
            results_limit: Number of results to retrieve (overrides default if provided)
            similarity_threshold: Similarity threshold for filtering (overrides default if provided)
        """
        try:
            # Check if vector database is enabled
            if not is_vector_db_enabled():
                logger.debug("Vector database disabled - skipping memory retrieval")
                return [], []

            limit = self.memory_results_limit
            threshold = self.memory_similarity_threshold

            logger.debug(f"Memory retrieval: limit={limit}, threshold={threshold}")

            try:
                # Vector DB is enabled - load memory functions and use them
                episodic_manager, long_term_manager = (
                    MemoryManagerFactory.create_both_memory_managers(
                        agent_name=self.agent_name, llm_connection=llm_connection
                    )
                )

                async def run_queries():
                    long_term_results, episodic_results = await asyncio.gather(
                        asyncio.to_thread(
                            long_term_manager.query_memory,
                            query=query,
                            session_id=session_id,
                            n_results=limit,
                            similarity_threshold=threshold,
                        ),
                        asyncio.to_thread(
                            episodic_manager.query_memory,
                            query=query,
                            session_id=session_id,
                            n_results=limit,
                            similarity_threshold=threshold,
                        ),
                    )

                    return long_term_results, episodic_results

                # Enforce timeout (10 seconds)
                long_term_results, episodic_results = await asyncio.wait_for(
                    run_queries(), timeout=10.0
                )
                return long_term_results, episodic_results

            except asyncio.TimeoutError:
                logger.warning("Memory queries timed out after 10 seconds")
                return [], []
            except ImportError as e:
                logger.warning(f"Memory management modules not available: {e}")
                return [], []
            except Exception as e:
                logger.error(f"Error in memory retrieval: {e}")
                return [], []

        except Exception as e:
            logger.error(f"Failed to retrieve memory for session {session_id}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return (
                "No relevant long-term memory found",
                "No relevant episodic memory found",
            )

    async def extract_action_or_answer(
        self,
        response: str,
        session_id: str,
        event_router: Callable,
        debug: bool = False,
    ) -> ParsedResponse:
        """Parse LLM response to extract a final answer or a tool action using XML format only."""
        try:
            # emit the agent thoughts each time
            agent_thoughts = re.search(r"<thought>(.*?)</thought>", response, re.DOTALL)
            if agent_thoughts:
                event = Event(
                    type=EventType.AGENT_THOUGHT,
                    payload=AgentThoughtPayload(
                        message=str(agent_thoughts.group(1).strip()),
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )
            tool_calls = []
            tool_call_blocks = []
            # Check for XML-style tool call format first
            if "<tool_calls>" in response and "</tool_calls>" in response:
                if debug:
                    logger.info("Multiple tool calls detected.")
                block_match = re.search(
                    r"<tool_calls>(.*?)</tool_calls>", response, re.DOTALL
                )
                if block_match:
                    tool_call_blocks = re.findall(
                        r"<tool_call>(.*?)</tool_call>", block_match.group(1), re.DOTALL
                    )

            elif "<tool_call>" in response and "</tool_call>" in response:
                if debug:
                    logger.info("Single tool call detected.")
                single_match = re.search(
                    r"<tool_call>(.*?)</tool_call>", response, re.DOTALL
                )
                tool_call_blocks = [single_match.group(1)] if single_match else []
            else:
                tool_call_blocks = []

            # Parse each <tool_call> block
            for block in tool_call_blocks:
                name_match = re.search(
                    r"<tool_name>(.*?)</tool_name>", block, re.DOTALL
                ) or re.search(r"<name>(.*?)</name>", block, re.DOTALL)
                args_match = re.search(
                    r"<parameters>(.*?)</parameters>", block, re.DOTALL
                ) or re.search(r"<args>(.*?)</args>", block, re.DOTALL)

                if not (name_match and args_match):
                    return ParsedResponse(
                        error="Invalid tool call format - missing name or parameters"
                    )

                tool_name = name_match.group(1).strip()
                args_str = args_match.group(1).strip()
                # Parse parameters (JSON or XML)
                args = {}
                if args_str.startswith("{") and args_str.endswith("}"):
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        return ParsedResponse(error=f"Invalid JSON in args: {str(e)}")
                else:
                    for key, value in re.findall(
                        r"<(\w+)>(.*?)</\1>", args_str, re.DOTALL
                    ):
                        value = value.strip()
                        # Try parsing as JSON if it looks like JSON
                        if (value.startswith("[") and value.endswith("]")) or (
                            value.startswith("{") and value.endswith("}")
                        ):
                            try:
                                args[key] = json.loads(value)
                            except json.JSONDecodeError:
                                args[key] = value
                        else:
                            args[key] = value

                tool_calls.append({"tool": tool_name, "parameters": args})

            # Return parsed tool calls if any
            if tool_calls:
                return ParsedResponse(action=True, data=json.dumps(tool_calls))

            # Check for XML final answer format
            if "<final_answer>" in response and "</final_answer>" in response:
                if debug:
                    logger.info(
                        "XML final answer format detected in response: %s", response
                    )
                final_answer_match = re.search(
                    r"<final_answer>(.*?)</final_answer>", response, re.DOTALL
                )
                if final_answer_match:
                    answer = final_answer_match.group(1).strip()
                    return ParsedResponse(answer=answer)
                else:
                    return ParsedResponse(error="Invalid XML final answer format")

            # Check if response contains any XML tags at all
            if "<" in response and ">" in response:
                # Has some XML but not the required format - return error
                return ParsedResponse(
                    error="Response contains XML tags but not in the required format. You MUST use <thought> and <final_answer> tags for all responses."
                )

            # No XML at all - return error
            return ParsedResponse(
                error="Response must use XML format. You MUST wrap your response in <thought> and <final_answer> tags. Example: <thought>Your reasoning here</thought><final_answer>Your answer here</final_answer>"
            )

        except Exception as e:
            logger.error("Error parsing model response: %s", str(e))
            return ParsedResponse(error=str(e))

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
        try:
            # Memory processing when vector DB is enabled
            if is_vector_db_enabled():
                try:
                    # get the name of the memory store type used
                    memory_store_type = message_history.__self__.memory_store_type

                    fire_and_forget_memory_processing(
                        session_id=session_id,
                        agent_name=self.agent_name,
                        messages=validated_messages,
                        memory_store_type=memory_store_type,
                        llm_connection=llm_connection,
                    )
                    logger.debug("Memory processing initiated")
                except ImportError as e:
                    logger.warning(f"Memory processing modules not available: {e}")
                except Exception as e:
                    logger.error(f"Error in memory processing: {e}")
            else:
                logger.debug("Vector DB disabled - skipping memory processing")
        except Exception as e:
            logger.error(f"Error in memory processing: {e}")
        # get the session state to be use
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        for message in validated_messages:
            role = message.role
            metadata = message.metadata

            if role == "user":
                # Only include real user messages — skip system-generated observations
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
    ) -> ToolError | list[ToolCallResult]:
        try:
            if self.enable_tools_knowledge_base:
                mcp_tools = None
                local_tools = tools_retriever_local_tool
                if self.memory_tool_backend:
                    build_tool_registry_memory_tool(
                        memory_tool_backend=self.memory_tool_backend,
                        registry=local_tools,
                    )

            actions = json.loads(parsed_response.data)
            if not isinstance(actions, list):
                actions = [actions]

            results: list[ToolCallResult] = []

            for action in actions:
                tool_name = action.get("tool", "").strip()
                tool_args = action.get("parameters", {})

                if not tool_name:
                    return ToolError(
                        observation="No tool name provided in the request",
                        tool_name="N/A",
                        tool_args=tool_args,
                    )

                mcp_tool_found = False
                tool_executor = None
                tool_data = {}

                # Check MCP tools
                if mcp_tools:
                    for server_name, tools in mcp_tools.items():
                        for tool in tools:
                            if tool.name.lower() == tool_name.lower():
                                mcp_tool_handler = MCPToolHandler(
                                    sessions=sessions,
                                    tool_data=json.dumps(action),
                                    mcp_tools=mcp_tools,
                                )
                                tool_executor = ToolExecutor(
                                    tool_handler=mcp_tool_handler
                                )
                                tool_data = (
                                    await mcp_tool_handler.validate_tool_call_request(
                                        tool_data=json.dumps(action),
                                        mcp_tools=mcp_tools,
                                    )
                                )
                                mcp_tool_found = True
                                break
                        if mcp_tool_found:
                            break

                # Check local tools
                if not mcp_tool_found and local_tools:
                    local_tool_handler = LocalToolHandler(local_tools=local_tools)
                    tool_executor = ToolExecutor(tool_handler=local_tool_handler)
                    tool_data = await local_tool_handler.validate_tool_call_request(
                        tool_data=json.dumps(action),
                        local_tools=local_tools,
                    )

                if not mcp_tool_found and not local_tools:
                    return ToolError(
                        observation=f"The tool named '{tool_name}' does not exist in the available tools.",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )

                if not tool_data.get("action"):
                    return ToolError(
                        observation=tool_data.get("error", "Tool validation failed"),
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )

                results.append(
                    ToolCallResult(
                        tool_executor=tool_executor,
                        tool_name=tool_data.get("tool_name"),
                        tool_args=normalize_tool_args(tool_data.get("tool_args")),
                    )
                )

            return results

        except Exception as e:
            logger.error(f"Error resolving tool call request: {e}")
            return ToolError(observation=str(e), tool_name="unknown", tool_args={})

    async def parse_tool_observation(self, raw_output: str) -> dict:
        """
        Normalizes and parses tool output into a **single, consistent structure**.

        Handles:
        - JSON string outputs
        - Aggregated multi-tool outputs
        - Old-style {successes:[], errors:[]} format
        - Non-JSON string errors

        Always returns:
        {
            "status": "success" | "partial" | "error",
            "tools_results": [
                {
                    "tool_name": str,
                    "args": dict | None,
                    "status": "success" | "error",
                    "data": dict | str | None,
                    "message": str | None,
                },
                ...
            ]
        }
        """
        try:
            if isinstance(raw_output, str):
                try:
                    parsed = json.loads(raw_output)
                except json.JSONDecodeError:
                    logger.warning(
                        "parse_tool_observation: raw_output is not valid JSON."
                    )
                    return {
                        "status": "error",
                        "tools_results": [
                            {
                                "tool_name": "unknown",
                                "args": None,
                                "status": "error",
                                "data": None,
                                "message": raw_output,
                            }
                        ],
                    }
            elif isinstance(raw_output, dict):
                parsed = raw_output
            else:
                return {
                    "status": "error",
                    "tools_results": [
                        {
                            "tool_name": "unknown",
                            "args": None,
                            "status": "error",
                            "data": None,
                            "message": str(raw_output),
                        }
                    ],
                }

            normalized_results = []

            if "tools_results" in parsed:
                raw_results = parsed["tools_results"]
            elif "successes" in parsed or "errors" in parsed:
                raw_results = []
                for s in parsed.get("successes", []):
                    raw_results.append({**s, "status": "success"})
                for e in parsed.get("errors", []):
                    raw_results.append({**e, "status": "error"})
            else:
                # Single-tool result fallback
                raw_results = [parsed]

            for item in raw_results:
                tool_name = item.get("tool_name") or item.get("tool") or "unknown"
                status = item.get("status", "success")
                args = item.get("args")

                data = item.get("data")
                message = item.get("message") or item.get("error")

                # Handle stringified JSON in data
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except json.JSONDecodeError:
                        pass

                normalized_results.append(
                    {
                        "tool_name": tool_name,
                        "args": args,
                        "status": status,
                        "data": data,
                        "message": message,
                    }
                )

            success_count = sum(
                1 for r in normalized_results if r["status"] == "success"
            )
            error_count = sum(1 for r in normalized_results if r["status"] == "error")

            if success_count > 0 and error_count == 0:
                global_status = "success"
            elif success_count > 0 and error_count > 0:
                global_status = "partial"
            else:
                global_status = "error"

            return {
                "status": global_status,
                "tools_results": normalized_results,
            }

        except Exception as e:
            logger.error(f"Error parsing tool observation: {e}", exc_info=True)
            return {
                "status": "error",
                "tools_results": [
                    {
                        "tool_name": "unknown",
                        "args": None,
                        "status": "error",
                        "data": None,
                        "message": f"Observation parsing failed: {str(e)}",
                    }
                ],
            }

    @track("tool_execution")
    async def act(
        self,
        parsed_response: ParsedResponse,
        response: str,
        add_message_to_history: Callable[[str, str, dict | None], Any],
        llm_connection: Callable,
        system_prompt: str,
        debug: bool = False,
        sessions: dict = None,
        mcp_tools: dict = None,
        local_tools: Any = None,
        session_id: str = None,
        event_router: Callable[[str, Event], Any] = None,
    ):
        # get the session state to be use
        session_state = self._get_session_state(session_id=session_id, debug=debug)

        tool_call_result = await self.resolve_tool_call_request(
            parsed_response=parsed_response,
            mcp_tools=mcp_tools,
            sessions=sessions,
            local_tools=local_tools,
        )

        tools_results = []
        obs_text = None

        # Early exit on tool validation failure
        if isinstance(tool_call_result, ToolError):
            tool_errors = (
                tool_call_result.errors
                if hasattr(tool_call_result, "errors")
                else [tool_call_result]
            )
            obs_text = (
                tool_call_result.observation
                if hasattr(tool_call_result, "observation")
                else str(tool_call_result)
            )

            for single_tool in tool_errors:
                tool_name = getattr(single_tool, "tool_name", "unknown")
                tool_args = getattr(single_tool, "tool_args", {})
                error_message = getattr(single_tool, "observation", obs_text)

                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=tool_name,
                        error_message=error_message,
                    ),
                    agent_name=self.agent_name,
                )

                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )
                session_state.loop_detector.record_tool_call(
                    str(tool_name),
                    str(tool_args),
                    str(error_message),
                )

            combined_tool_name = "_and_".join(
                [getattr(t, "tool_name", "unknown") for t in tool_errors]
            )
            combined_tool_args = [getattr(t, "tool_args", {}) for t in tool_errors]

            logger.error(
                f"Tool call validation failed for: {combined_tool_name} "
                f"args={combined_tool_args} -> {obs_text}"
            )
            # Populate tools_results for validation errors
            for single_tool in tool_errors:
                tools_results.append({
                    "tool_name": getattr(single_tool, "tool_name", "unknown"),
                    "args": getattr(single_tool, "tool_args", {}),
                    "status": "error",
                    "data": None,
                    "message": getattr(single_tool, "observation", obs_text)
                })
        else:
            tool_call_id = str(uuid.uuid4())
            combined_tool_name = "_and_".join([t.tool_name for t in tool_call_result])
            combined_tool_args = [t.tool_args for t in tool_call_result]

            tool_calls_metadata = ToolCallMetadata(
                agent_name=self.agent_name,
                has_tool_calls=True,
                tool_call_id=tool_call_id,
                tool_calls=[
                    ToolCall(
                        id=tool_call_id,
                        function=ToolFunction(
                            name=combined_tool_name[:60],
                            arguments=json.dumps(combined_tool_args),
                        ),
                    )
                ],
            )

            # Add tool call started event
            event = Event(
                type=EventType.TOOL_CALL_STARTED,
                payload=ToolCallStartedPayload(
                    tool_name=combined_tool_name,
                    tool_args=json.dumps(combined_tool_args),
                    tool_call_id=tool_call_id,
                ),
                agent_name=self.agent_name,
            )
            if event_router:
                self.background_task_manager.run_background_strict(
                    event_router(session_id=session_id, event=event)
                )

            await add_message_to_history(
                role="assistant",
                content=response,
                metadata=tool_calls_metadata.model_dump(),
                session_id=session_id,
            )
            session_state.messages.append(Message(role="assistant", content=response))

            try:
                async with asyncio.timeout(self.tool_call_timeout):
                    metadata = {
                        "top_k": self.tools_results_limit,
                        "similarity_threshold": self.tools_similarity_threshold,
                    }

                    first_executor = tool_call_result[0].tool_executor
                    tool_output = await first_executor.execute(
                        agent_name=self.agent_name,
                        tool_args=combined_tool_args,
                        tool_name=combined_tool_name,
                        tool_call_id=tool_call_id,
                        add_message_to_history=add_message_to_history,
                        llm_connection=llm_connection,
                        mcp_tools=mcp_tools,
                        session_id=session_id,
                        **metadata,
                    )

                # Parse tool output into structured observation
                observation = await self.parse_tool_observation(tool_output)

                tools_results = observation.get("tools_results", [])
                obs_lines = []
                success_count = 0
                error_count = 0

                # Normalize tool_call_result into list for safety
                if not isinstance(tool_call_result, (list, tuple)):
                    tool_call_result = [tool_call_result]

                # Process each tool result
                tool_counter = defaultdict(int)
                seen_tools: set[str] = set()
                for single_tool, result in zip(tool_call_result, tools_results):
                    tool_name = result.get("tool_name", "unknown_tool")
                    args = result.get("args", {})
                    status = result.get("status", "unknown")
                    data = result.get("data")
                    message = result.get("message", "")
                    # Increment counter for repeated tool names
                    tool_counter[tool_name] += 1
                    tool_call_generated_id = f"{tool_name}#{tool_counter[tool_name]}"
                    display_value = data if data is not None else message
                    # Record in loop detector only once per batch
                    if tool_name not in seen_tools:
                        seen_tools.add(tool_name)
                        session_state.loop_detector.record_tool_call(
                            str(tool_name),
                            str(args),
                            str(display_value),
                        )

                    if status == "success":
                        obs_lines.append(f"{tool_call_generated_id}: {display_value}")
                        success_count += 1
                    elif status == "error":
                        # Include detailed reason if available
                        reason = display_value or "Unknown error occurred."
                        obs_lines.append(f"{tool_call_generated_id} ERROR: {reason}")
                        error_count += 1
                    else:
                        obs_lines.append(
                            f"{tool_call_generated_id}: Unexpected status '{status}'"
                        )
                        error_count += 1
                # Clear the seen_tools after finishing the batch
                seen_tools.clear()
                if success_count == len(tools_results):
                    status = "success"
                    obs_text = "\n\n".join(obs_lines)
                elif success_count > 0 and error_count > 0:
                    status = "partial"
                    obs_text = "Partial success:\n" + "\n\n".join(obs_lines)
                elif error_count == len(tools_results):
                    status = "error"
                    # Combine all messages into one readable explanation
                    error_details = "\n\n".join(obs_lines)
                    obs_text = f"Tool execution failed completely:\n{error_details}"
                else:
                    status = observation.get("status", "unknown")
                    obs_text = "\n\n".join(obs_lines) or "No valid tool results."

                # Handle tool call result event
                event = Event(
                    type=EventType.TOOL_CALL_RESULT,
                    payload=ToolCallResultPayload(
                        tool_name=combined_tool_name,
                        tool_args=json.dumps(combined_tool_args),
                        result=obs_text,
                        tool_call_id=tool_call_id,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

            except asyncio.TimeoutError:
                obs_text = (
                    "Tool call timed out. Please try again or use a different approach."
                )
                logger.warning(obs_text)
                for single_tool in tool_call_result:
                    session_state.loop_detector.record_tool_call(
                        str(single_tool.tool_name),
                        str(single_tool.tool_args),
                        obs_text,
                    )

                # Populate tools_results for error
                for single_tool in tool_call_result:
                    tools_results.append({
                        "tool_name": getattr(single_tool, "tool_name", "unknown"),
                        "args": getattr(single_tool, "tool_args", {}),
                        "status": "error",
                        "data": None,
                        "message": obs_text
                    })

                await add_message_to_history(
                    role="tool",
                    content=obs_text,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "agent_name": self.agent_name,
                    },
                    session_id=session_id,
                )

                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=combined_tool_name,
                        error_message=obs_text,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

            except Exception as e:
                obs_text = f"Error executing tool: {str(e)}"
                logger.error(obs_text)
                for single_tool in tool_call_result:
                    session_state.loop_detector.record_tool_call(
                        str(single_tool.tool_name),
                        str(single_tool.tool_args),
                        obs_text,
                    )

                # Populate tools_results for error
                for single_tool in tool_call_result:
                    tools_results.append({
                        "tool_name": getattr(single_tool, "tool_name", "unknown"),
                        "args": getattr(single_tool, "tool_args", {}),
                        "status": "error",
                        "data": None,
                        "message": obs_text
                    })

                await add_message_to_history(
                    role="tool",
                    content=obs_text,
                    metadata={
                        "tool_call_id": tool_call_id,
                        "agent_name": self.agent_name,
                    },
                    session_id=session_id,
                )
                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=combined_tool_name,
                        error_message=obs_text,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

        # Debug and final observation handling
        if debug:
            show_tool_response(
                agent_name=self.agent_name,
                tool_name=combined_tool_name,
                tool_args=combined_tool_args,
                observation=obs_text,
            )

        xml_obs_block = build_xml_observations_block(tools_results)
        session_state.messages.append(
            Message(
                role="user",
                content=xml_obs_block,
            )
        )
        await add_message_to_history(
            role="user",
            content=xml_obs_block,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )

        if debug:
            logger.info(
                f"Agent state changed from {session_state.state} to {AgentState.OBSERVING}"
            )
        session_state.state = AgentState.OBSERVING

        if isinstance(tool_call_result, (list, tuple)):
            tool_call_results = list(tool_call_result)
        else:
            tool_call_results = [tool_call_result]

        for single_tool in tool_call_results:
            tool_name = getattr(single_tool, "tool_name", None)
            if not tool_name:
                if isinstance(single_tool, (list, tuple)) and len(single_tool) >= 1:
                    tool_name = single_tool[0]
                else:
                    logger.warning(
                        "Skipping malformed tool_call_result item: %s", single_tool
                    )
                    continue

            if session_state.loop_detector.is_looping(tool_name):
                loop_type = session_state.loop_detector.get_loop_type(tool_name)
                logger.warning(
                    f"Tool call loop detected for '{tool_name}': {loop_type}"
                )

                new_system_prompt = handle_stuck_state(system_prompt)
                session_state.messages = await self.reset_system_prompt(
                    messages=session_state.messages,
                    system_prompt=new_system_prompt,
                )

                loop_message = (
                    f"Observation:\n"
                    f"⚠️ Tool call loop detected for '{tool_name}': {loop_type}\n\n"
                    "Current approach is not working. You MUST now provide a final answer to the user.\n"
                    "Please:\n"
                    "1. Stop trying the same approach\n"
                    "2. Provide your best response to the user based on what you know\n"
                    "3. Use <final_answer>Your response here</final_answer> format\n"
                    "4. Be helpful and explain any limitations if needed\n"
                    "5. Do NOT continue with more tool calls\n"
                    "\nYou MUST respond with <final_answer> tags now.\n"
                )

                # handle loop detection event
                event = Event(
                    type=EventType.TOOL_CALL_ERROR,
                    payload=ToolCallErrorPayload(
                        tool_name=tool_name,
                        error_message=loop_message,
                    ),
                    agent_name=self.agent_name,
                )
                if event_router:
                    self.background_task_manager.run_background_strict(
                        event_router(session_id=session_id, event=event)
                    )

                session_state.messages.append(
                    Message(role="user", content=loop_message)
                )

                if debug:
                    logger.info(
                        f"Agent state changed from {session_state.state} to {AgentState.STUCK}"
                    )

                session_state.state = AgentState.STUCK
                session_state.loop_detector.reset(tool_name)

    async def reset_system_prompt(self, messages: list, system_prompt: str):
        # Reset system prompt and keep all messages
        old_messages = messages[1:]
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(old_messages)
        return messages

    @asynccontextmanager
    async def agent_session_state_context(
        self, new_state: AgentState, session_id: str, debug: bool
    ):
        """Context manager to change the agent session state"""
        # get the session state to be use
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

    async def get_tools_registry(
        self, mcp_tools: dict = None, local_tools: Any = None
    ) -> str:
        lines = ["Available tools:"]

        def format_param_type(param_info: dict) -> str:
            """Format parameter type with nested structure details."""
            p_type = param_info.get("type", "any")

            # Handle array types
            if p_type == "array":
                items = param_info.get("items", {})
                if items:
                    item_type = items.get("type", "any")
                    if item_type == "object":
                        # Array of objects - show the object structure
                        props = items.get("properties", {})
                        if props:
                            fields = ", ".join(
                                [
                                    f'"{k}": {v.get("type", "any")}'
                                    for k, v in props.items()
                                ]
                            )
                            return f"array of objects ({{{fields}}})"
                        return "array of objects"
                    else:
                        return f"array of {item_type}s"
                return "array"

            # Handle object types
            elif p_type == "object":
                props = param_info.get("properties", {})
                if props:
                    fields = ", ".join(
                        [f'"{k}": {v.get("type", "any")}' for k, v in props.items()]
                    )
                    return f"object ({{{fields}}})"
                return "object"

            return p_type

        def format_param_description(param_info: dict) -> str:
            """Format parameter description with structure examples."""
            p_desc = param_info.get("description", "").replace("\n", " ").strip()
            p_type = param_info.get("type", "any")

            # Add structure hints for complex types
            if p_type == "array":
                items = param_info.get("items", {})
                if items.get("type") == "object":
                    props = items.get("properties", {})
                    if props:
                        # Build example structure
                        example_fields = []
                        for k, v in props.items():
                            v_type = v.get("type", "any")
                            if v_type == "string":
                                example_fields.append(f'"{k}": "..."')
                            elif v_type == "number":
                                example_fields.append(f'"{k}": 0')
                            elif v_type == "boolean":
                                example_fields.append(f'"{k}": true')
                            else:
                                example_fields.append(f'"{k}": ...')

                        example = "{" + ", ".join(example_fields) + "}"
                        if p_desc:
                            p_desc += f". Example: {example}"
                        else:
                            p_desc = f"Example: {example}"

            return p_desc if p_desc else "No description"

        try:
            # Process local tools
            if self.enable_tools_knowledge_base:
                local_tools = tools_retriever_local_tool
            if self.memory_tool_backend:
                build_tool_registry_memory_tool(
                    memory_tool_backend=self.memory_tool_backend,
                    registry=local_tools,
                )
            if local_tools:
                if self.memory_tool_backend and not self.enable_tools_knowledge_base:
                    build_tool_registry_memory_tool(
                        memory_tool_backend=self.memory_tool_backend,
                        registry=local_tools,
                    )
                local_tools_list = local_tools.get_available_tools()
                if local_tools_list:
                    for tool in local_tools_list:
                        if isinstance(tool, dict):
                            name = tool.get("name", "unknown")
                            desc = (
                                tool.get("description", "").replace("\n", " ").strip()
                            )
                            lines.append(f"\n{name}: {desc}")
                            input_schema = tool.get("inputSchema", {})
                            params = input_schema.get("properties", {})
                            required = input_schema.get("required", [])
                            if params:
                                for param_name, param_info in params.items():
                                    p_type = format_param_type(param_info)
                                    p_desc = format_param_description(param_info)
                                    is_req = (
                                        " (required)" if param_name in required else ""
                                    )
                                    lines.append(
                                        f"  - {param_name}: {p_type}{is_req} — {p_desc}"
                                    )

            # Process MCP tools
            if mcp_tools and not self.enable_tools_knowledge_base:
                for server_name, tools in mcp_tools.items():
                    if not tools:
                        continue
                    for tool in tools:
                        if hasattr(tool, "name"):
                            name = str(tool.name)
                            desc = str(tool.description).replace("\n", " ").strip()
                            lines.append(f"\n{name}: {desc}")
                            if hasattr(tool, "inputSchema") and tool.inputSchema:
                                params = tool.inputSchema.get("properties", {})
                                required = tool.inputSchema.get("required", [])
                                for param_name, param_info in params.items():
                                    p_type = format_param_type(param_info)
                                    p_desc = format_param_description(param_info)
                                    is_req = (
                                        " (required)" if param_name in required else ""
                                    )
                                    lines.append(
                                        f"  - {param_name}: {p_type}{is_req} — {p_desc}"
                                    )

            if len(lines) == 1:
                return "No tools available"
        except Exception as e:
            logger.error(f"Error building compact tool registry: {e}")
            return "No tools available"

        return "\n".join(lines)

    async def prepare_initial_messages(
        self,
        session_state,
        system_prompt: str,
        query: str,
        session_id: str,
        llm_connection: Callable,
        message_history: Callable[[], Any],
        mcp_tools: dict = None,
        local_tools: Any = None,
        debug: bool = False,
    ) -> None:
        """
        Prepare the full initial message list for the LLM by concurrently:
        - Retrieving memory (if enabled)
        - Building tool registry
        - Loading prior message history
        - Injecting current user query
        """
        tasks = {}

        # Memory retrieval only if vector DB enabled
        if is_vector_db_enabled():
            tasks["memory"] = self.get_long_episodic_memory(
                query=query, session_id=session_id, llm_connection=llm_connection
            )
        else:
            tasks["memory"] = asyncio.create_task(asyncio.sleep(0, result=([], [])))

        # Tool registry
        tasks["tools"] = self.get_tools_registry(
            mcp_tools=mcp_tools, local_tools=local_tools
        )

        # Working memory: load prior history and load to session_state.messages
        tasks["history"] = self.update_llm_working_memory(
            message_history=message_history,
            session_id=session_id,
            llm_connection=llm_connection,
            debug=debug,
        )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    tasks["memory"],
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
            # fallback results on timeout
            results = [([], []), "No tools available", None]

        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"prepare_initial_messages error: {r}", exc_info=True)

        # Unpack results
        long_term_memory, episodic_memory = (
            results[0] if not isinstance(results[0], BaseException) else ([], [])
        )
        tools_section = (
            results[1]
            if not isinstance(results[1], BaseException)
            else "No tools available"
        )

        # Build system prompt
        updated_system_prompt = system_prompt

        if is_vector_db_enabled():
            updated_system_prompt += f"\n[LONG TERM MEMORY]\n{long_term_memory}\n[EPISODIC MEMORY]\n{episodic_memory}"

        if self.enable_tools_knowledge_base:
            updated_system_prompt += f"\n{tools_retriever_additional_prompt}"

        if self.memory_tool_backend:
            updated_system_prompt += f"\n{memory_tool_additional_prompt}"

        updated_system_prompt += f"\n[AVAILABLE TOOLS REGISTRY]\n{tools_section}"

        current_date_time = date_time_func["format_date"]()
        updated_system_prompt += (
            f"<current_date_time>{current_date_time}</current_date_time>"
        )

        # Insert system prompt at index 0
        session_state.messages.insert(
            0, Message(role="system", content=updated_system_prompt)
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
    ) -> str | None:
        """Execute ReAct loop with JSON communication
        kwargs: if mcp is enbale then it will be sessions and availables_tools else it will be local_tools
        """
        # get the session state to be use it must be new at all time
        session_state = self._get_session_state(session_id=session_id, debug=debug)
        session_state.messages = []
        session_state.assistant_with_tool_calls = None
        session_state.pending_tool_responses = []
        session_state.loop_detector.reset()

        # handle start of agent run
        event = Event(
            type=EventType.USER_MESSAGE,
            payload=UserMessagePayload(
                message=query,
            ),
            agent_name=self.agent_name,
        )
        if event_router:
            self.background_task_manager.run_background_strict(
                event_router(session_id=session_id, event=event)
            )

        await add_message_to_history(
            role="user",
            content=query,
            session_id=session_id,
            metadata={"agent_name": self.agent_name},
        )
        await self.prepare_initial_messages(
            system_prompt=system_prompt,
            query=query,
            session_state=session_state,
            llm_connection=llm_connection,
            message_history=message_history,
            mcp_tools=mcp_tools,
            local_tools=local_tools,
            session_id=session_id,
            debug=debug,
        )
        # check if the agent is in a valid state to run
        if session_state.state not in [
            AgentState.IDLE,
            AgentState.ERROR,
        ]:
            raise RuntimeError(
                f"Agent is not in a valid state to run: {session_state.state}"
            )

        # set the agent state to running
        async with self.agent_session_state_context(
            new_state=AgentState.RUNNING, session_id=session_id, debug=debug
        ):
            current_steps = 0
            last_valid_response = None
            while (
                session_state.state not in [AgentState.FINISHED]
                and current_steps < self.max_steps
            ):
                # logger.info(f"history: {(session_state.messages)}")
                if debug:
                    logger.info(
                        f"Sending {len(session_state.messages)} messages to LLM"
                    )
                current_steps += 1
                if self._limits_enabled:
                    self.usage_limits.check_before_request(usage=usage)

                try:

                    @track("llm_call")
                    async def make_llm_call():
                        return await llm_connection.llm_call(session_state.messages)

                    response = await make_llm_call()

                    if response:
                        # handle agent response event
                        event = Event(
                            type=EventType.AGENT_MESSAGE,
                            payload=AgentMessagePayload(
                                message=str(response),
                            ),
                            agent_name=self.agent_name,
                        )
                        if event_router:
                            self.background_task_manager.run_background_strict(
                                event_router(session_id=session_id, event=event)
                            )

                        if hasattr(response, "usage"):
                            request_usage = Usage(
                                requests=current_steps,
                                request_tokens=response.usage.prompt_tokens,
                                response_tokens=response.usage.completion_tokens,
                                total_tokens=response.usage.total_tokens,
                            )
                            usage.incr(request_usage)

                            # Only enforce limits if they're enabled
                            if self._limits_enabled:
                                # Check if we've exceeded token limits
                                self.usage_limits.check_tokens(usage)
                                # Show remaining resources
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
                            else:
                                # Just log usage without limits
                                if debug:
                                    logger.info(
                                        f"Usage recorded (limits disabled): "
                                        f"Request Tokens: {request_usage.request_tokens}, "
                                        f"Response Tokens: {request_usage.response_tokens}, "
                                        f"Total Tokens: {request_usage.total_tokens}"
                                    )

                        if hasattr(response, "choices"):
                            response = response.choices[0].message.content.strip()
                        elif hasattr(response, "message"):
                            response = response.message.content.strip()
                    else:
                        raise Exception("No response from LLM")
                except UsageLimitExceeded as e:
                    error_message = f"Usage limit error: {e}"
                    logger.error(error_message)
                    return error_message
                except Exception as e:
                    error_message = f"LLM error: {e}"
                    logger.error(e)
                    return error_message

                parsed_response = await self.extract_action_or_answer(
                    response=response,
                    debug=debug,
                    session_id=session_id,
                    event_router=event_router,
                )
                if debug:
                    logger.info(f"current steps: {current_steps}")
                # check for final answer
                if parsed_response.answer is not None:
                    last_valid_response = parsed_response.answer

                    session_state.messages.append(
                        Message(
                            role="assistant",
                            content=parsed_response.answer,
                        )
                    )

                    # handle final answer event
                    event = Event(
                        type=EventType.FINAL_ANSWER,
                        payload=FinalAnswerPayload(
                            message=str(parsed_response.answer),
                        ),
                        agent_name=self.agent_name,
                    )
                    if event_router:
                        self.background_task_manager.run_background_strict(
                            event_router(session_id=session_id, event=event)
                        )
                    await add_message_to_history(
                        role="assistant",
                        content=parsed_response.answer,
                        session_id=session_id,
                        metadata={"agent_name": self.agent_name},
                    )

                    session_state.state = AgentState.FINISHED
                    return parsed_response.answer

                # check for action
                if parsed_response.action is not None:
                    # Execute the action
                    @track("action_execution")
                    async def execute_action():
                        await self.act(
                            parsed_response=parsed_response,
                            response=response,
                            add_message_to_history=add_message_to_history,
                            system_prompt=system_prompt,
                            llm_connection=llm_connection,
                            mcp_tools=mcp_tools,
                            debug=debug,
                            sessions=sessions,
                            local_tools=local_tools,
                            session_id=session_id,
                            event_router=event_router,
                        )

                    await execute_action()

                if parsed_response.error is not None:
                    logger.error(f"Error in parsed response: {parsed_response.error}")
                    # we need to continue the loop if there is an error in parsing
                    session_state.messages.append(
                        Message(
                            role="user",
                            content=(
                                f"{parsed_response.error}\n\n"
                                "Error in your response parsing. Please follow the response format strictly. "
                                "If the issue persists, provide a final answer to the user and stop."
                            ),
                        )
                    )
                    continue
                # check for stuck state
                if current_steps >= self.max_steps:
                    session_state.state = AgentState.STUCK
                    if last_valid_response:
                        # Prepend max steps context for judge evaluation
                        max_steps_context = f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit]\n\n"
                        return max_steps_context + last_valid_response
                    else:
                        return f"[SYSTEM_CONTEXT: MAX_STEPS_REACHED - Agent hit {self.max_steps} step limit without valid response]"

        # If we exit the loop due to STUCK state, return last valid response with context
        if session_state.state == AgentState.STUCK and last_valid_response:
            # Prepend loop detection context for judge evaluation
            loop_context = (
                "[SYSTEM_CONTEXT: LOOP_DETECTED - Agent stuck in tool call loop]\n\n"
            )
            return loop_context + last_valid_response

        return None
