"""Per-app OmniServe HTTP request metrics."""

import time
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger

if TYPE_CHECKING:
    from .config import OmniServeConfig


class OmniServeMetrics:
    """In-process request metrics for a single OmniServe app instance."""

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

    def inc_counter(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe_histogram(self, name: str, value: float) -> None:
        observations = self.histograms.setdefault(name, [])
        observations.append(value)
        if len(observations) > 1000:
            self.histograms[name] = observations[-1000:]

    def inc_gauge(self, name: str, value: float = 1) -> None:
        self.gauges[name] = self.gauges.get(name, 0) + value

    def dec_gauge(self, name: str, value: float = 1) -> None:
        self.gauges[name] = self.gauges.get(name, 0) - value

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines: list[str] = []

        for name, value in self.counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        for name, value in self.gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        for name, values in self.histograms.items():
            if not values:
                continue
            count = len(values)
            total = sum(values)
            lines.append(f"# TYPE {name} summary")
            lines.append(f"{name}_count {count}")
            lines.append(f"{name}_sum {total:.6f}")
            lines.append(f"{name}_avg {total / count:.6f}")

        return "\n".join(lines) + "\n"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware for collecting per-app request metrics."""

    def __init__(self, app, metrics: OmniServeMetrics):
        super().__init__(app)
        self.metrics = metrics

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Track request count, status, duration, and active requests."""
        if request.url.path == "/prometheus":
            return await call_next(request)

        self.metrics.inc_gauge("omniserve_active_requests")
        self.metrics.inc_counter("omniserve_requests_total")

        start_time = time.time()
        is_error = False

        try:
            response = await call_next(request)
            is_error = response.status_code >= 400
            return response
        except Exception:
            is_error = True
            raise
        finally:
            duration = time.time() - start_time
            self.metrics.dec_gauge("omniserve_active_requests")
            self.metrics.observe_histogram(
                "omniserve_request_duration_seconds", duration
            )

            if is_error:
                self.metrics.inc_counter("omniserve_requests_error")
            else:
                self.metrics.inc_counter("omniserve_requests_success")

            path = request.url.path.replace("/", "_").strip("_") or "root"
            self.metrics.inc_counter(f"omniserve_requests_{path}_total")


def add_prometheus_endpoint(app: FastAPI) -> None:
    """Add the Prometheus text endpoint."""

    @app.get(
        "/prometheus",
        tags=["Metrics"],
        response_class=PlainTextResponse,
        summary="Prometheus metrics",
        description="OmniServe HTTP request metrics in Prometheus text format.",
    )
    async def prometheus_metrics(request: Request):
        metrics: OmniServeMetrics = request.app.state.omniserve_metrics
        return PlainTextResponse(
            content=metrics.to_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    logger.info("OmniServe: Prometheus metrics endpoint enabled at /prometheus")


def setup_metrics(app: FastAPI, config: "OmniServeConfig") -> None:
    """Install per-app request metrics."""
    _ = config
    metrics = OmniServeMetrics()
    app.state.omniserve_metrics = metrics
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    add_prometheus_endpoint(app)
    logger.info("OmniServe: HTTP request metrics enabled")
