"""OmniServe observability.

Provides lightweight in-process request metrics and a Prometheus endpoint.
Agent-level traces are built from OmniCoreAgent events, not external tracing
libraries.
"""

import time
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

if TYPE_CHECKING:
    from .config import OmniServeConfig


class SimpleMetrics:
    """Simple metrics tracker for Prometheus-style output."""

    def __init__(self):
        self.counters: dict[str, int] = {
            "omniserve_requests_total": 0,
            "omniserve_requests_success": 0,
            "omniserve_requests_error": 0,
        }
        self.histograms: dict[str, list[float]] = {
            "omniserve_request_duration_seconds": [],
        }
        self.gauges: dict[str, float] = {
            "omniserve_active_requests": 0,
        }

    def inc_counter(self, name: str, value: int = 1):
        if name in self.counters:
            self.counters[name] += value
        else:
            self.counters[name] = value

    def observe_histogram(self, name: str, value: float):
        if name not in self.histograms:
            self.histograms[name] = []
        self.histograms[name].append(value)
        # Keep last 1000 observations
        if len(self.histograms[name]) > 1000:
            self.histograms[name] = self.histograms[name][-1000:]

    def set_gauge(self, name: str, value: float):
        self.gauges[name] = value

    def inc_gauge(self, name: str, value: float = 1):
        if name in self.gauges:
            self.gauges[name] += value
        else:
            self.gauges[name] = value

    def dec_gauge(self, name: str, value: float = 1):
        if name in self.gauges:
            self.gauges[name] -= value

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []

        # Counters
        for name, value in self.counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # Gauges
        for name, value in self.gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        # Histograms (simplified - just show count and sum)
        for name, values in self.histograms.items():
            if values:
                count = len(values)
                total = sum(values)
                avg = total / count if count > 0 else 0
                lines.append(f"# TYPE {name} summary")
                lines.append(f"{name}_count {count}")
                lines.append(f"{name}_sum {total:.6f}")
                lines.append(f"{name}_avg {avg:.6f}")

        return "\n".join(lines) + "\n"


# Global metrics instance
_metrics = SimpleMetrics()


def get_metrics() -> SimpleMetrics:
    """Get the global metrics instance."""
    return _metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting request metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Track request metrics."""
        # Skip metrics endpoint to avoid recursion
        if request.url.path == "/prometheus":
            return await call_next(request)

        metrics = get_metrics()

        # Track active requests
        metrics.inc_gauge("omniserve_active_requests")
        metrics.inc_counter("omniserve_requests_total")

        start_time = time.time()
        is_error = False

        try:
            response = await call_next(request)
            if response.status_code >= 400:
                is_error = True
            return response
        except Exception:
            is_error = True
            raise
        finally:
            duration = time.time() - start_time
            metrics.dec_gauge("omniserve_active_requests")
            metrics.observe_histogram("omniserve_request_duration_seconds", duration)

            if is_error:
                metrics.inc_counter("omniserve_requests_error")
            else:
                metrics.inc_counter("omniserve_requests_success")

            # Track by endpoint
            path = request.url.path.replace("/", "_").strip("_") or "root"
            metrics.inc_counter(f"omniserve_requests_{path}_total")


def add_prometheus_endpoint(app: FastAPI) -> None:
    """
    Add Prometheus metrics endpoint at /prometheus.

    Args:
        app: FastAPI application
    """

    @app.get(
        "/prometheus",
        tags=["Observability"],
        response_class=PlainTextResponse,
        summary="Prometheus metrics",
        description="Metrics in Prometheus text format",
    )
    async def prometheus_metrics():
        """Return metrics in Prometheus format."""
        return PlainTextResponse(
            content=get_metrics().to_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    logger.info("OmniServe: Prometheus metrics endpoint enabled at /prometheus")


def add_metrics_middleware(app: FastAPI) -> None:
    """
    Add metrics collection middleware.

    Args:
        app: FastAPI application
    """
    app.add_middleware(MetricsMiddleware)
    logger.info("OmniServe: Metrics collection middleware enabled")


def setup_observability(
    app: FastAPI,
    config: "OmniServeConfig",
    service_name: str = "omniserve",
) -> None:
    """
    Set up all observability features.

    Args:
        app: FastAPI application
        config: OmniServe configuration
        service_name: Service name for tracing
    """
    # Add metrics middleware (always enabled)
    add_metrics_middleware(app)

    add_prometheus_endpoint(app)
