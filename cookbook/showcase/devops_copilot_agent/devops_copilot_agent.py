import asyncio
import time
from typing import Dict, Any, Optional

from config import ProductionConfig
from bash import Bash
from system_prompt import get_system_prompt
from omnicoreagent import (
    OmniCoreAgent,
    ToolRegistry,
    MemoryRouter,
    EventRouter,
)
from not_ready.background_agent_service import BackgroundAgentService
from observability import (
    get_metrics_collector,
    get_logger,
    AuditLogger,
    HealthChecker,
    RateLimiter,
    perf,
)

# --------------------------------------------------------------
# 1. Load & Validate Config
# --------------------------------------------------------------
CONFIG = ProductionConfig.load()
CONFIG.validate()

# --------------------------------------------------------------
# 2. Observability (config-driven)
# --------------------------------------------------------------
log = get_logger(
    name="copilot",
    level=CONFIG.observability.log_level,
    fmt=CONFIG.observability.log_format,
    file=CONFIG.observability.log_file,
    max_bytes=CONFIG.observability.log_max_bytes,
    backup=CONFIG.observability.log_backup_count,
)

# Singleton metrics collector
metrics = get_metrics_collector(CONFIG.observability.enable_metrics)

audit = AuditLogger(CONFIG.security.audit_log_file)
health = HealthChecker()
rate_limiter = RateLimiter(
    max_req=CONFIG.security.rate_limit_requests,
    window=CONFIG.security.rate_limit_window,
)

# Health checks
health.add("config", lambda: True)
health.add("redis", lambda: CONFIG.storage.memory_store_type == "redis")


# tool input schema
def to_json_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Safe bash command (ls, docker ps, kubectl get, etc.)",
            }
        },
        "required": ["cmd"],
    }


# initialized tool registry to add local tools (local functions)
tool_registry = ToolRegistry()


# --------------------------------------------------------------
# 5. Runner
# --------------------------------------------------------------
class DevOpsCopilotRunner:
    def __init__(self):
        self.cfg = CONFIG
        self.agent: Optional[OmniCoreAgent] = None
        self.bg_service: Optional[BackgroundAgentService] = None
        self.memory_router: Optional[MemoryRouter] = None
        self.event_router: Optional[EventRouter] = None
        self.bash = Bash(
            cwd=self.cfg.devops.working_directory,
            timeout_seconds=self.cfg.devops.timeout_seconds,
            max_output_chars=self.cfg.devops.max_output_chars,
            enable_history=self.cfg.devops.enable_history,
            max_history_size=self.cfg.devops.max_history_size,
        )
        self.system_prompt = get_system_prompt(
            allowed_commands=self.bash.ALLOWED_COMMANDS
        )
        self.connected = False
        # the session_id is hardcoded for me to use it always same session_id you can swith to str(uuid.uuid()) that will always auto generate new session_id
        self.session_id = "abiorh0001"

        metrics.session_start()
        log.info(f"Session started: {self.session_id}")

    async def initialize(self):
        if self.connected:
            return

        log.info("Initializing")

        # Memory & Event Router
        self.memory_router = MemoryRouter(self.cfg.storage.memory_store_type)
        self.event_router = EventRouter(self.cfg.storage.event_store_type)

        if self.cfg.observability.enable_metrics:
            if metrics.start_server(self.cfg.observability.metrics_port):
                log.info(
                    f"Prometheus metrics server started on :{self.cfg.observability.metrics_port}"
                )
            else:
                log.debug("Metrics server already running")

        @tool_registry.register_tool(
            name="exec_bash_command",
            description="Execute safe DevOps bash command",
            inputSchema=to_json_schema(),
        )
        @perf(metrics)
        def exec_bash_command(cmd: str) -> dict:
            # Rate limiting check
            if self.cfg.security.enable_rate_limiting:
                if not rate_limiter.allow(self.session_id):
                    metrics.record_rate_limit(self.session_id)
                    audit.command(cmd, self.session_id, status="rate_limited")
                    return {"status": "error", "error": "rate limit exceeded"}

            result = self.bash.exec_bash_command(cmd)

            stderr = result.get("data", {}).get("stderr", "")

            if "[BLOCKED]" in stderr:
                reason = "unknown"
                if "explicitly prohibited for security" in stderr:
                    reason = "blacklist"
                elif "not in the allowed list" in stderr:
                    reason = "not_allowed"
                elif "write operation" in stderr:
                    reason = "write_operation"
                elif "parsing failed" in stderr:
                    reason = "parse_error"

                blocked_count = stderr.count("[BLOCKED]")
                for _ in range(blocked_count):
                    metrics.record_blocked_command(reason=reason)

                log.warning(f"Blocked {blocked_count} command(s) in: {cmd[:100]}")

            # Audit logging
            audit.command(
                cmd,
                self.session_id,
                status=result.get("status", "unknown"),
                blocked="[BLOCKED]" in stderr,
            )

            return result

        self.agent = OmniCoreAgent(
            name=self.cfg.agent.name,
            system_instruction=self.system_prompt,
            model_config={
                "provider": self.cfg.model.provider,
                "model": self.cfg.model.model,
                "temperature": self.cfg.model.temperature,
                "top_p": self.cfg.model.top_p,
                "max_context_length": self.cfg.model.max_context_length,
            },
            local_tools=tool_registry,
            agent_config={
                "agent_name": self.cfg.agent.name,
                "max_steps": self.cfg.agent.max_steps,
                "tool_call_timeout": self.cfg.agent.tool_call_timeout,
                "request_limit": self.cfg.agent.request_limit,
                "total_tokens_limit": self.cfg.agent.total_tokens_limit,
                # Memory with summarization
                "memory_config": {
                    "mode": self.cfg.agent.memory_mode,
                    "value": self.cfg.agent.memory_window_size,
                    "summary": {
                        "enabled": True,
                        "retention_policy": "summarize",
                    },
                },
                # Context management for long sessions
                "context_management": {
                    "enabled": True,
                    "mode": "token_budget",
                    "value": 100000,
                    "threshold_percent": 75,
                    "strategy": "summarize_and_truncate",
                    "preserve_recent": 6,
                },
                # Prompt injection guardrails
                "guardrail_config": {
                    "enabled": True,
                    "strict_mode": True,
                    "fail_action": "block",
                },
                "memory_results_limit": self.cfg.agent.memory_results_limit,
                "memory_similarity_threshold": self.cfg.agent.memory_similarity_threshold,
                "enable_tools_knowledge_base": self.cfg.agent.enable_tools_knowledge_base,
                "tools_results_limit": self.cfg.agent.tools_results_limit,
                "tools_similarity_threshold": self.cfg.agent.tools_similarity_threshold,
                "memory_tool_backend": self.cfg.agent.memory_tool_backend,
            },
            memory_router=self.memory_router,
            event_router=self.event_router,
            debug=True,
        )

        if not self.agent.is_event_store_available():
            log.warning("Event store not available")

        self.connected = True
        log.info("Ready")

        metrics.set_health(True)

    async def handle_chat(
        self, query: str, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Handle chat with LLM query tracking"""

        if not self.connected:
            await self.initialize()

        start_time = time.time()
        status = "success"

        try:
            result = await self.agent.run(query=query, session_id=session_id)
            response = result.get("response", "")
            
            # Log agent metrics
            agent_metrics = await self.agent.get_metrics()
            log.info(f"Agent metrics: {agent_metrics.get('total_requests', 0)} requests, "
                     f"{agent_metrics.get('total_tokens', 0)} tokens")
            
            audit.query(query, session_id, len(response))
            return {
                "response": response,
                "metrics": result.get("metric"),
            }

        except asyncio.TimeoutError:
            status = "timeout"
            log.error(f"Query timeout: {query}")
            return None

        except Exception as e:
            status = "error"
            log.error(f"Agent error: {e}")
            return None

        finally:
            duration = time.time() - start_time
            metrics.record_llm_query(duration, status)
            log.info(f"LLM query completed in {duration:.2f}s with status: {status}")
