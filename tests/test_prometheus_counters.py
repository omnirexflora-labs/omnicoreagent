"""/prometheus counts since the server started, and says what runs did.

The support desk ramp (2026-10-07) read ``omniserve_request_duration_seconds_count``
stuck at 1000 under load: the figure came from the last 1000 samples, so a
rate() over it read zero while the server served thousands of requests.
Prometheus needs ``_count``, ``_sum`` and buckets that only ever grow.
"""

from __future__ import annotations

import re

from omnicoreagent.core.metrics import COUNTERS
from omnicoreagent.serve.metrics import OmniServeMetrics


def _value(text: str, series: str) -> float:
    match = re.search(rf"^{re.escape(series)} (\S+)$", text, re.MULTILINE)
    assert match, f"{series} not in:\n{text}"
    return float(match.group(1))


def test_histogram_count_and_sum_do_not_stop_at_a_thousand():
    metrics = OmniServeMetrics()
    for _ in range(2500):
        metrics.observe_histogram("omniserve_request_duration_seconds", 0.5)
    text = metrics.to_prometheus()
    assert _value(text, "omniserve_request_duration_seconds_count") == 2500
    assert _value(text, "omniserve_request_duration_seconds_sum") == 1250.0
    assert "# TYPE omniserve_request_duration_seconds histogram" in text


def test_histogram_buckets_are_cumulative_and_end_at_the_count():
    metrics = OmniServeMetrics()
    for value in (0.003, 0.2, 0.2, 7.0, 500.0):
        metrics.observe_histogram("omniserve_request_duration_seconds", value)
    text = metrics.to_prometheus()
    name = "omniserve_request_duration_seconds_bucket"
    assert _value(text, f'{name}{{le="0.005"}}') == 1
    assert _value(text, f'{name}{{le="0.25"}}') == 3
    assert _value(text, f'{name}{{le="10"}}') == 4
    assert _value(text, f'{name}{{le="+Inf"}}') == 5


def test_a_labelled_counter_is_exposed_with_its_labels():
    COUNTERS.inc("omniserve_test_events_total", kind="a")
    COUNTERS.inc("omniserve_test_events_total", kind="a")
    COUNTERS.inc("omniserve_test_events_total", kind='b"c')
    text = "\n".join(COUNTERS.prometheus_lines())
    assert "# TYPE omniserve_test_events_total counter" in text
    assert _value(text, 'omniserve_test_events_total{kind="a"}') == 2
    assert _value(text, 'omniserve_test_events_total{kind="b\\"c"}') == 1


def test_a_counter_has_bounded_label_sets():
    for i in range(500):
        COUNTERS.inc("omniserve_test_bounded_total", who=f"id-{i}")
    text = "\n".join(COUNTERS.prometheus_lines())
    series = [line for line in text.splitlines() if line.startswith("omniserve_test_bounded_total{")]
    assert len(series) <= 101
    assert any('who="other"' in line for line in series)
    assert sum(float(line.rsplit(" ", 1)[1]) for line in series) == 500


def test_the_server_exposes_the_registry():
    from omnicoreagent.serve.metrics import setup_metrics
    from fastapi import FastAPI

    app = FastAPI()
    setup_metrics(app, None)
    COUNTERS.inc("omniserve_test_exposed_total")
    assert "omniserve_test_exposed_total" in app.state.omniserve_metrics.to_prometheus()
