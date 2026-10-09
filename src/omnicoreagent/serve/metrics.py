"""Per-app OmniServe HTTP request metrics."""

import re
import time
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from omnicoreagent.core.logging import logger
from omnicoreagent.core.metrics import COUNTERS

if TYPE_CHECKING:
    from .config import OmniServeConfig


# Upper bounds in seconds; a request or a run step takes milliseconds to minutes.
HISTOGRAM_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300,
)


class _Histogram:
    """Counts since the process started, which is what Prometheus needs.

    The first version kept the last 1000 samples and reported their count and
    sum, so under load both stopped growing at 1000 and ``rate()`` read zero
    (the support desk ramp, 2026-10-07).
    """

    def __init__(self) -> None:
        self.count = 0
        self.sum = 0.0
        self.buckets = [0] * len(HISTOGRAM_BUCKETS)

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        for i, bound in enumerate(HISTOGRAM_BUCKETS):
            if value <= bound:
                self.buckets[i] += 1


class OmniServeMetrics:
    """In-process request metrics for a single OmniServe app instance."""

    def __init__(self):
        self.counters: dict[str, int] = {
            "omniserve_requests_total": 0,
            "omniserve_requests_success": 0,
            "omniserve_requests_error": 0,
        }
        self.histograms: dict[str, _Histogram] = {
            "omniserve_request_duration_seconds": _Histogram(),
        }
        self.gauges: dict[str, float] = {
            "omniserve_active_requests": 0,
        }
        # Callables returning ready-made exposition lines, for numbers some
        # other part of the server owns (the run admission limit).
        self.collectors: list[Callable[[], list[str]]] = []

    def inc_counter(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe_histogram(self, name: str, value: float) -> None:
        self.histograms.setdefault(name, _Histogram()).observe(value)

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

        for name, histogram in self.histograms.items():
            if not histogram.count:
                continue
            lines.append(f"# TYPE {name} histogram")
            # Buckets are cumulative: each counts every sample at or under it.
            for bound, seen in zip(HISTOGRAM_BUCKETS, histogram.buckets):
                lines.append(f'{name}_bucket{{le="{bound:g}"}} {seen}')
            lines.append(f'{name}_bucket{{le="+Inf"}} {histogram.count}')
            lines.append(f"{name}_sum {histogram.sum:.6f}")
            lines.append(f"{name}_count {histogram.count}")

        for collect in self.collectors:
            lines.extend(collect())

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

            # Named for the route, not the path: /prometheus needs no token,
            # and a counter per path showed the run and session ids other
            # callers used, and grew by one for every path tried (the rc7
            # security review).
            route = getattr(request.scope.get("route"), "path", None)
            name = re.sub(r"[^a-zA-Z0-9]+", "_", route).strip("_") if route else "unmatched"
            self.metrics.inc_counter(f"omniserve_requests_{name or 'root'}_total")


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
    # The runtime's own counters (runs, models, budgets, approvals).
    metrics.collectors.append(COUNTERS.prometheus_lines)
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    add_prometheus_endpoint(app)
    logger.info("OmniServe: HTTP request metrics enabled")
