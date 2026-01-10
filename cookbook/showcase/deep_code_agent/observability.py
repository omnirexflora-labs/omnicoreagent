import logging
import time
import asyncio
import json
from functools import wraps
from typing import Optional, Callable, Dict, Any
from datetime import datetime
from collections import defaultdict
from threading import Lock

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server, REGISTRY

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# ================================================
# ENHANCED METRICS COLLECTOR
# ================================================


class MetricsCollector:
    """Enhanced metrics collector with full dashboard support"""

    _instance = None
    _lock = Lock()

    def __new__(cls, enable: bool = True):
        """Singleton pattern"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, enable: bool = True):
        if self._initialized:
            return

        self.enabled = enable and PROMETHEUS_AVAILABLE
        self._server_started = False
        self._initialized = True

        if not self.enabled:
            return

        # === COMMAND METRICS (from @perf decorator) ===
        self.function_calls = Counter(
            "copilot_commands_total", "Total function calls", ["function", "status"]
        )
        self.function_duration = Histogram(
            "copilot_command_seconds",
            "Function execution duration",
            ["function", "type"],
        )

        # === SESSION METRICS ===
        self.active_sessions = Gauge(
            "copilot_sessions_active", "Number of currently active sessions"
        )

        # === BLOCKED COMMANDS ===
        self.blocked_commands = Counter(
            "copilot_blocked_total", "Total blocked/rejected commands", ["reason"]
        )

        # === LLM QUERY METRICS ===
        self.llm_queries = Counter(
            "copilot_queries_total", "Total LLM queries", ["status"]
        )
        self.llm_query_duration = Histogram(
            "copilot_query_seconds",
            "LLM query duration in seconds",
            ["status"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
        )

        # === RATE LIMITING ===
        self.rate_limited = Counter(
            "copilot_rate_limited_total", "Total rate limited requests", ["session_id"]
        )

        # === SYSTEM HEALTH ===
        self.health_status = Gauge(
            "copilot_health_status", "Overall health status (1=healthy, 0=unhealthy)"
        )
        self.health_status.set(1)

    # === SESSION MANAGEMENT ===
    def session_start(self):
        """Increment active sessions counter"""
        if self.enabled:
            self.active_sessions.inc()

    def session_end(self):
        """Decrement active sessions counter"""
        if self.enabled:
            self.active_sessions.dec()

    # === BLOCKED COMMANDS ===
    def record_blocked_command(self, reason: str = "blacklist"):
        """Record a blocked command attempt"""
        if self.enabled:
            self.blocked_commands.labels(reason=reason).inc()

    # === LLM QUERIES ===
    def record_llm_query(self, duration: float, status: str = "success"):
        """
        Record LLM query with duration

        Args:
            duration: Query duration in seconds
            status: Query status (success, error, timeout)
        """
        if self.enabled:
            self.llm_queries.labels(status=status).inc()
            self.llm_query_duration.labels(status=status).observe(duration)

    # === RATE LIMITING ===
    def record_rate_limit(self, session_id: str):
        """Record a rate limit hit"""
        if self.enabled:
            self.rate_limited.labels(session_id=session_id).inc()

    # === HEALTH ===
    def set_health(self, healthy: bool):
        """Set overall health status"""
        if self.enabled:
            self.health_status.set(1 if healthy else 0)

    # === SERVER MANAGEMENT ===
    def start_server(self, port: int = 8000) -> bool:
        """
        Start Prometheus metrics HTTP server

        Returns:
            True if server started, False if already running or disabled
        """
        if not self.enabled or self._server_started:
            return False

        try:
            start_http_server(port, addr="0.0.0.0")
            self._server_started = True
            return True
        except OSError:
            return False

    # === PERFORMANCE DECORATOR ===
    def track_performance(self, func: Callable) -> Callable:
        """
        Decorator to track function performance
        Works with both sync and async functions
        """

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not self.enabled:
                return func(*args, **kwargs)

            start = time.time()
            status = "success"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start
                self.function_calls.labels(function=func.__name__, status=status).inc()
                self.function_duration.labels(
                    function=func.__name__, type="command"
                ).observe(duration)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not self.enabled:
                return await func(*args, **kwargs)

            start = time.time()
            status = "success"
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start
                self.function_calls.labels(function=func.__name__, status=status).inc()
                self.function_duration.labels(
                    function=func.__name__, type="command"
                ).observe(duration)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    # === EXPORT ===
    def export(self) -> str:
        """Export metrics in Prometheus format"""
        if not self.enabled:
            return "Metrics disabled"

        from prometheus_client import generate_latest

        return generate_latest(REGISTRY).decode("utf-8")


# === SINGLETON GETTER ===
_metrics_instance: Optional[MetricsCollector] = None


def get_metrics_collector(enable: bool = True) -> MetricsCollector:
    """Get or create the singleton MetricsCollector instance"""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = MetricsCollector(enable=enable)
    return _metrics_instance


# === CONVENIENCE DECORATOR ===
def perf(metrics_collector: Optional[MetricsCollector] = None):
    """
    Convenience decorator for performance tracking

    Usage:
        @perf(metrics)
        def my_function():
            pass
    """

    def decorator(func: Callable) -> Callable:
        if metrics_collector is None:
            return func
        return metrics_collector.track_performance(func)

    return decorator


# ================================================
# LOGGING
# ================================================


def get_logger(
    name: str = "copilot",
    level: str = "INFO",
    fmt: str = "json",
    file: Optional[str] = None,
    max_bytes: int = 10485760,
    backup: int = 3,
) -> logging.Logger:
    """Get configured logger"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if fmt == "json":
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

    if file:
        from logging.handlers import RotatingFileHandler

        handler = RotatingFileHandler(file, maxBytes=max_bytes, backupCount=backup)
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


# ================================================
# AUDIT LOGGER
# ================================================


class AuditLogger:
    """Audit logger for security and operational events"""

    def __init__(self, log_file: str = "audit.log"):
        self.log_file = log_file

    def _write(self, event_type: str, data: Dict[str, Any]):
        """Write audit event as JSON line"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            **data,
        }
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def command(self, cmd: str, session_id: str, status: str, blocked: Any = None):
        """Log command execution (for DevOps compatibility)"""
        self._write(
            "command",
            {
                "cmd": cmd,
                "session_id": session_id,
                "status": status,
                "blocked": blocked,
            },
        )

    def query(self, query: str, session_id: str, response_len: int):
        """Log user query and response length"""
        self._write(
            "query",
            {
                "query": query[:100],
                "session_id": session_id,
                "response_len": response_len,
            },
        )

    def tool_call(self, tool_name: str, session_id: str, parameters: Dict[str, Any]):
        """Log agent tool invocation with parameters"""
        self._write(
            "tool_call",
            {
                "tool_name": tool_name,
                "session_id": session_id,
                "parameters": parameters,
            },
        )


# ================================================
# HEALTH CHECKER
# ================================================


class HealthChecker:
    """System health checker"""

    def __init__(self):
        self.checks: Dict[str, Callable] = {}

    def add(self, name: str, check: Callable):
        """Add health check"""
        self.checks[name] = check

    def run(self) -> Dict[str, Any]:
        """Run all health checks"""
        results = {}
        for name, check in self.checks.items():
            try:
                results[name] = check()
            except Exception as e:
                results[name] = {"error": str(e)}

        results["overall"] = all(
            v is True or (isinstance(v, dict) and not v.get("error"))
            for v in results.values()
        )
        return results


# ================================================
# RATE LIMITER
# ================================================


class RateLimiter:
    """Simple in-memory rate limiter"""

    def __init__(self, max_req: int = 1, window: int = 60):
        self.max_req = max_req
        self.window = window
        self.requests = defaultdict(list)

    def allow(self, key: str) -> bool:
        """Check if request is allowed"""
        now = time.time()
        self.requests[key] = [ts for ts in self.requests[key] if now - ts < self.window]

        if len(self.requests[key]) >= self.max_req:
            return False

        self.requests[key].append(now)
        return True
