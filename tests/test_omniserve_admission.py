"""OmniServe admits only so many concurrent runs, and says 503 beyond that.

The support desk ramp (2026-10-07): on a 2-core container throughput topped
out at 3-4.5 requests a second, and chat p50 was 37 s at 100 users. Every
request was accepted and all of them slowed down together. A process now
takes only as many runs as one event loop can serve well, makes the others wait a short
while, then tells them to retry.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from omnicoreagent import OmniCoreAgent
from omnicoreagent.serve import OmniServe, OmniServeConfig
from omnicoreagent.serve.admission import (
    DEFAULT_MAX_CONCURRENT_RUNS,
    default_max_concurrent_runs,
)


def _server(max_runs, wait=0.3, **extra):
    agent = MagicMock(spec=OmniCoreAgent)
    agent.name = "A"
    agent.generate_session_id.return_value = "s"
    state = {"gate": None, "started": []}

    async def run(*args, **kwargs):
        state["started"].append(1)
        await state["gate"].wait()
        return {"response": "ok"}

    agent.run = AsyncMock(side_effect=run)
    agent.resume = AsyncMock(side_effect=run)
    agent.get_run = AsyncMock(return_value={})
    config = OmniServeConfig(
        max_concurrent_runs=max_runs, run_admission_wait_seconds=wait, **extra
    )
    return OmniServe(agent=agent, config=config).app, state


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def _until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        assert loop.time() < end, "condition never became true"
        await asyncio.sleep(0.01)


async def _hold_one(client, state):
    """Start one run that stays in flight until the gate opens."""
    state["gate"] = asyncio.Event()
    task = asyncio.create_task(client.post("/run/sync", json={"query": "a"}))
    await _until(lambda: state["started"])
    return task


@pytest.mark.asyncio
async def test_a_request_over_the_limit_waits_then_gets_503_with_retry_after():
    app, state = _server(1, wait=0.3)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        second = await client.post("/run/sync", json={"query": "b"})
        waited = loop.time() - t0
        assert second.status_code == 503
        assert second.headers["retry-after"].isdigit()
        body = second.json()
        assert body["error"] == "ServerBusy"
        assert body["max_concurrent_runs"] == 1
        assert waited >= 0.25
        assert len(state["started"]) == 1
        state["gate"].set()
        assert (await first).status_code == 200


@pytest.mark.asyncio
async def test_a_slot_freed_in_time_lets_the_waiting_request_through():
    app, state = _server(1, wait=3.0)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        second = asyncio.create_task(client.post("/run/sync", json={"query": "b"}))
        await asyncio.sleep(0.2)
        assert not second.done()
        state["gate"].set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200


@pytest.mark.asyncio
async def test_an_sse_request_over_the_limit_gets_503_before_streaming():
    app, state = _server(1, wait=0.2)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        second = await client.post("/run", json={"query": "b"})
        assert second.status_code == 503
        assert second.headers["content-type"].startswith("application/json")
        assert "retry-after" in second.headers
        state["gate"].set()
        await first


@pytest.mark.asyncio
async def test_a_failed_run_releases_its_slot():
    app, state = _server(1, wait=0.2)
    app.state.agent.run = AsyncMock(side_effect=RuntimeError("boom"))
    async with _client(app) as client:
        for _ in range(3):
            resp = await client.post("/run/sync", json={"query": "a"})
            assert resp.status_code == 500


@pytest.mark.asyncio
async def test_routes_that_start_no_run_are_never_limited():
    app, state = _server(1, wait=0.2)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        for path in ("/health", "/ready", "/prometheus", "/runs", "/tools"):
            resp = await client.get(path)
            assert resp.status_code != 503, path
        state["gate"].set()
        await first


@pytest.mark.asyncio
async def test_resume_is_limited_and_decisions_are_not():
    app, state = _server(1, wait=0.2)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        resumed = await client.post("/runs/r1/resume")
        assert resumed.status_code == 503
        approval = await client.post(
            "/runs/r1/approvals/a1", json={"decision": "approve", "approver": "me"}
        )
        assert approval.status_code != 503
        state["gate"].set()
        await first


@pytest.mark.asyncio
async def test_the_metrics_show_runs_in_flight_and_rejected():
    app, state = _server(1, wait=0.1)
    async with _client(app) as client:
        first = await _hold_one(client, state)
        await client.post("/run/sync", json={"query": "b"})
        text = (await client.get("/prometheus")).text
        assert "omniserve_runs_in_flight 1" in text
        assert "omniserve_runs_rejected_total 1" in text
        assert "omniserve_runs_limit 1" in text
        state["gate"].set()
        await first
        text = (await client.get("/prometheus")).text
        assert "omniserve_runs_in_flight 0" in text


@pytest.mark.asyncio
async def test_zero_means_unlimited():
    app, state = _server(0, wait=0.1)
    state["gate"] = asyncio.Event()
    async with _client(app) as client:
        tasks = [
            asyncio.create_task(client.post("/run/sync", json={"query": str(i)}))
            for i in range(30)
        ]
        await _until(lambda: len(state["started"]) == 30)
        state["gate"].set()
        responses = await asyncio.gather(*tasks)
        assert all(r.status_code == 200 for r in responses)


@pytest.mark.asyncio
async def test_an_sse_stream_holds_its_slot_until_it_ends(monkeypatch):
    from omnicoreagent.serve.routes import runs

    release = asyncio.Event()

    async def fake_stream(*args, **kwargs):
        yield "data: one\n\n"
        await release.wait()
        yield "data: two\n\n"

    monkeypatch.setattr(runs, "run_agent_stream", fake_stream)
    app, state = _server(1, wait=0.1)
    async with _client(app) as client:
        stream = asyncio.create_task(client.post("/run", json={"query": "a"}))
        await _until(lambda: app.state.run_admission.in_flight == 1)
        busy = await client.post("/run/sync", json={"query": "b"})
        assert busy.status_code == 503
        release.set()
        assert (await stream).status_code == 200
        assert app.state.run_admission.in_flight == 0


def test_the_default_is_24_whatever_the_cpu_count(monkeypatch):
    # The knee measured on a server (2026-10-07): one process on a 2-CPU cap
    # peaked at 5.5 runs a second and used about 1.1 cores, so a bigger cap or
    # more cores must not raise the limit. More CPUs means more processes.
    assert DEFAULT_MAX_CONCURRENT_RUNS == 24
    for cpus in (1, 2, 8, 64):
        monkeypatch.setattr("os.cpu_count", lambda cpus=cpus: cpus)
        monkeypatch.setattr(
            "os.sched_getaffinity", lambda _pid, cpus=cpus: set(range(cpus)),
            raising=False,
        )
        assert default_max_concurrent_runs() == 24


def test_the_app_starts_with_24_runs_when_nothing_is_set():
    app, _ = _server(None)
    assert app.state.run_admission.limit == 24


def test_the_setting_overrides_the_default(monkeypatch):
    assert OmniServeConfig().max_concurrent_runs is None  # the default, 24, is applied at startup
    assert OmniServeConfig().run_admission_wait_seconds == 5.0
    monkeypatch.setenv("OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS", "7")
    monkeypatch.setenv("OMNICOREAGENT_SERVE_RUN_ADMISSION_WAIT", "1.5")
    cfg = OmniServeConfig(max_concurrent_runs=99)
    assert cfg.max_concurrent_runs == 7
    assert cfg.run_admission_wait_seconds == 1.5
    for off in ("0", "none", "None", "unlimited"):
        monkeypatch.setenv("OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS", off)
        assert OmniServeConfig().max_concurrent_runs == 0
    monkeypatch.setenv("OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS", "lots")
    with pytest.raises(ValueError):
        OmniServeConfig()
    monkeypatch.setenv("OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS", "-2")
    with pytest.raises(ValueError):
        OmniServeConfig()
