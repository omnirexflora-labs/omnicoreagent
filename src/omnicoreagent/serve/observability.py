"""
OmniServe Observability - OpenTelemetry Integration.

Provides distributed tracing, metrics, and Prometheus endpoint.
Requires: opentelemetry-api, opentelemetry-sdk, opentelemetry-instrumentation-fastapi

Optional dependencies:
    pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-prometheus
    pip install opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-httpx
"""

import time
from typing import TYPE_CHECKING, Dict, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.utils import logger

if TYPE_CHECKING:
    from .config import OmniServeConfig


# Check if OpenTelemetry is available
OTEL_AVAILABLE = False
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    OTEL_AVAILABLE = True
except ImportError:
    pass


# Simple in-memory metrics for when OpenTelemetry is not installed
class SimpleMetrics:
    """Simple metrics tracker for Prometheus-style output."""

    def __init__(self):
        self.counters: Dict[str, int] = {
            "omniserve_requests_total": 0,
            "omniserve_requests_success": 0,
            "omniserve_requests_error": 0,
        }
        self.histograms: Dict[str, list] = {
            "omniserve_request_duration_seconds": [],
        }
        self.gauges: Dict[str, float] = {
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


def setup_opentelemetry(app: FastAPI, service_name: str = "omniserve") -> None:
    """
    Set up OpenTelemetry tracing and metrics.

    Args:
        app: FastAPI application
        service_name: Name for the service in traces
    """
    if not OTEL_AVAILABLE:
        logger.warning(
            "OmniServe: OpenTelemetry not installed. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-instrumentation-fastapi"
        )
        return

    try:
        # Create resource
        resource = Resource.create({SERVICE_NAME: service_name})

        # Set up tracing
        tracer_provider = TracerProvider(resource=resource)

        # Add console exporter for development (replace with OTLP for production)
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(tracer_provider)

        # Instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)

        logger.info("OmniServe: OpenTelemetry tracing enabled")

    except Exception as e:
        logger.warning(f"OmniServe: Failed to setup OpenTelemetry: {e}")


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

    # Add Prometheus endpoint
    add_prometheus_endpoint(app)

    # Set up OpenTelemetry if available
    if OTEL_AVAILABLE:
        setup_opentelemetry(app, service_name)
