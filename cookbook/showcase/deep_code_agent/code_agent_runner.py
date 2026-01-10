import asyncio
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from tools.coding_tools import register_coding_tools
from system_prompt import get_deep_coding_system_prompt
from omnicoreagent import OmniCoreAgent, ToolRegistry, MemoryRouter, EventRouter
from observability_globals import metrics, audit, rate_limiter, CONFIG, log
from sandbox.sandbox_executor import SandboxExecutor


class DeepCodingAgentRunner:
    def __init__(self):
        self.cfg = CONFIG
        # self.session_id = str(uuid.uuid4())
        self.session_id = "abiorh001"
        self.agent: Optional[OmniCoreAgent] = None
        self.memory_router: Optional[MemoryRouter] = None
        self.event_router: Optional[EventRouter] = None
        self.connected = False

        # Create session workspace (for sandbox code)
        session_ws = Path(self.cfg.coding.workspace_root) / self.session_id
        session_ws.mkdir(parents=True, exist_ok=True)

        self.sandbox_executor = SandboxExecutor(
            workspace_root=str(self.cfg.coding.workspace_root),
            timeout=self.cfg.coding.sandbox_timeout_seconds,
            memory_mb=self.cfg.coding.sandbox_memory_mb,
        )

        metrics.session_start()
        log.info(f"Deep Coding Agent session started: {self.session_id}")

    async def initialize(self):
        if self.connected:
            return

        log.info("Initializing Deep Coding Agent")

        # Routers
        self.memory_router = MemoryRouter(self.cfg.storage.memory_store_type)
        self.event_router = EventRouter(self.cfg.storage.event_store_type)

        # Start metrics server if enabled
        if self.cfg.observability.enable_metrics:
            if metrics.start_server(self.cfg.observability.metrics_port):
                log.info(
                    f"Prometheus metrics server started on :{self.cfg.observability.metrics_port}"
                )
            else:
                log.debug("Metrics server already running")

        # Tool Registry
        tool_registry = ToolRegistry()
        register_coding_tools(tool_registry=tool_registry, runner_instance=self)

        # Build agent config with full OmniCoreAgent features
        agent_config = {
            "agent_name": self.cfg.agent.name,
            "max_steps": self.cfg.agent.max_steps,
            "tool_call_timeout": self.cfg.agent.tool_call_timeout,
            "request_limit": self.cfg.agent.request_limit,
            "total_tokens_limit": self.cfg.agent.total_tokens_limit,
            # Memory with summarization for long coding sessions
            "memory_config": {
                "mode": self.cfg.agent.memory_mode,
                "value": self.cfg.agent.memory_window_size,
                "summary": {
                    "enabled": True,
                    "retention_policy": "summarize",
                },
            },
            # Context management for complex coding tasks
            "context_management": {
                "enabled": True,
                "mode": "token_budget",
                "value": 100000,
                "threshold_percent": 75,
                "strategy": "summarize_and_truncate",
                "preserve_recent": 8,  # Keep more context for code
            },
            # Guardrails for code execution safety
            "guardrail_config": {
                "enabled": True,
                "strict_mode": True,
                "fail_action": "block",
            },
            "memory_tool_backend": self.cfg.agent.memory_tool_backend,
        }

        self.agent = OmniCoreAgent(
            name=self.cfg.agent.name,
            system_instruction=get_deep_coding_system_prompt(
                session_id=self.session_id
            ),
            model_config={
                "provider": self.cfg.model.provider,
                "model": self.cfg.model.model,
                "temperature": self.cfg.model.temperature,
                "top_p": self.cfg.model.top_p,
                "max_context_length": self.cfg.model.max_context_length,
            },
            local_tools=tool_registry,
            agent_config=agent_config,
            memory_router=self.memory_router,
            event_router=self.event_router,
            debug=True,
        )

        if not self.agent.is_event_store_available():
            log.warning("Event store not available")
        await self.agent.connect_mcp_servers()
        self.connected = True
        log.info("Deep Coding Agent is ready")
        metrics.set_health(True)

    async def handle_chat(self, query: str) -> Optional[Dict[str, Any]]:
        if not self.connected:
            await self.initialize()

        start_time = time.time()
        status = "success"

        try:
            # Rate limiting (per session)
            if self.cfg.security.enable_rate_limiting:
                if not rate_limiter.allow(self.session_id):
                    metrics.record_rate_limit(self.session_id)
                    audit.query(query, self.session_id, 0)
                    return {
                        "status": "error",
                        "response": "Rate limit exceeded! Try again later.",
                        "session_id": self.session_id,
                    }

            result = await self.agent.run(query=query, session_id=self.session_id)
            response = result.get("response", "")
            audit.query(query, self.session_id, len(response))
            return {
                "status": "success",
                "response": response,
                "session_id": self.session_id,
                "workspace_path": str(
                    Path(self.cfg.coding.workspace_root) / self.session_id
                ),
            }

        except asyncio.TimeoutError:
            status = "timeout"
            log.error(f"Query timeout: {query}")
            return None

        except Exception as e:
            status = "error"
            log.error(f"Agent error: {e}", exc_info=True)
            return None

        finally:
            duration = time.time() - start_time
            metrics.record_llm_query(duration, status)
            log.info(f"LLM query completed in {duration:.2f}s with status: {status}")
