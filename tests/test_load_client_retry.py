"""The support desk load client retries a 503 the way a real client does.

The ramp at 100 users (2026-10-07): the admission limit answered some resume
calls 503 with a ``Retry-After``, the load client did not retry them, and 119
approved runs were never resumed. A client that ignores ``Retry-After`` is not
measuring what a user would see. The count of 503s, of requests that
succeeded after a retry and of requests that gave up are kept apart, so the
report can say how often the limit pushed back and whether it was enough.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LOAD = Path(__file__).resolve().parent.parent / "apps" / "support_desk" / "load"


def _loadlib():
    sys.path.insert(0, str(LOAD))
    try:
        spec = importlib.util.spec_from_file_location("support_desk_loadlib", LOAD / "loadlib.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LOAD))
    return module


loadlib = _loadlib()


def _response(status, retry_after=None):
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return SimpleNamespace(status_code=status, headers=headers)


class Sender:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.statuses.pop(0)


class Clock:
    """Time that only moves when the helper sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _retry(clock, **kwargs):
    return loadlib.RetryAfter(
        sleep=clock.sleep, clock=clock.monotonic, rng=SimpleNamespace(uniform=lambda low, high: high), **kwargs
    )


@pytest.mark.asyncio
async def test_a_503_with_retry_after_is_waited_out_and_retried():
    clock, retry = Clock(), None
    retry = _retry(clock)
    send = Sender(_response(503, 2), _response(503, 1), _response(200))

    response, retries = await retry.run(send)

    assert response.status_code == 200 and retries == 2
    assert send.calls == 3
    # Each wait is the header plus a little jitter, never less than the header.
    assert 2 <= clock.slept[0] <= 2.5 and 1 <= clock.slept[1] <= 1.5
    assert (retry.seen_503, retry.succeeded_after_retry, retry.gave_up) == (2, 1, 0)


@pytest.mark.asyncio
async def test_a_request_that_never_stops_being_refused_gives_up_within_its_budget():
    clock = Clock()
    retry = _retry(clock, budget=10.0)
    send = Sender(*[_response(503, 4)] * 10)

    response, retries = await retry.run(send)

    assert response.status_code == 503
    assert clock.now <= 10.0, "never waits past the budget"
    assert retries == send.calls - 1
    assert retry.gave_up == 1 and retry.succeeded_after_retry == 0
    assert retry.seen_503 == send.calls


@pytest.mark.asyncio
async def test_a_503_without_retry_after_is_not_retried():
    clock = Clock()
    retry = _retry(clock)
    send = Sender(_response(503))

    response, retries = await retry.run(send)

    assert response.status_code == 503 and retries == 0 and send.calls == 1
    assert (retry.seen_503, retry.succeeded_after_retry, retry.gave_up) == (1, 0, 1)


@pytest.mark.asyncio
async def test_other_answers_pass_through_untouched():
    clock = Clock()
    retry = _retry(clock)

    for status in (200, 409, 500):
        response, retries = await retry.run(Sender(_response(status)))
        assert response.status_code == status and retries == 0

    assert clock.slept == []
    assert (retry.seen_503, retry.succeeded_after_retry, retry.gave_up) == (0, 0, 0)


@pytest.mark.asyncio
async def test_counts_are_kept_per_kind_of_request():
    clock = Clock()
    retry = _retry(clock)

    await retry.run(Sender(_response(503, 1), _response(200)), kind="resume")
    await retry.run(Sender(_response(503, 1), _response(200)), kind="approve")
    await retry.run(Sender(_response(200)), kind="chat")

    assert retry.by_kind == {
        "resume": {"seen_503": 1, "succeeded_after_retry": 1, "gave_up": 0},
        "approve": {"seen_503": 1, "succeeded_after_retry": 1, "gave_up": 0},
    }


@pytest.mark.asyncio
async def test_the_desk_client_retries_a_503_on_every_kind_of_call():
    import httpx

    answers = [httpx.Response(503, headers={"Retry-After": "0"}, json={}), httpx.Response(200, json={"ok": 1})]

    def handler(request):
        return answers.pop(0)

    desk = loadlib.Desk("http://desk.invalid", "token")
    desk.client = httpx.AsyncClient(base_url="http://desk.invalid", transport=httpx.MockTransport(handler))
    try:
        body, error = await desk.call("resume", "POST", "/runs/r1/resume")
    finally:
        await desk.close()

    assert error is None and body == {"ok": 1}
    assert desk.records[-1].retries == 1
    assert desk.retry.summary()["succeeded_after_retry"] == 1


class FakeDesk:
    """A desk whose runs finish when told, standing in for the sweep."""

    def __init__(self, statuses):
        self.statuses = dict(statuses)

    async def quiet(self, method, path, **kwargs):
        return {"status": self.statuses[path.rsplit("/", 1)[1]]}, None


def _attempt(run_id, decision="approve", outcome="not_completed"):
    return loadlib.Attempt("2001", 100, f"s-{run_id}", decision, run_id=run_id, outcome=outcome)


@pytest.mark.asyncio
async def test_the_drain_waits_for_the_sweep_and_counts_a_resumed_run_as_completed():
    clock = Clock()
    attempts = [_attempt("run_a"), _attempt("run_b", "deny"), _attempt("run_c", outcome="completed")]
    desk = FakeDesk({"run_a": "awaiting_approval", "run_b": "awaiting_approval"})

    async def sleep(seconds):
        await clock.sleep(seconds)
        # The sweep resumes the runs a while after the grace period.
        if clock.now >= 40:
            desk.statuses.update(run_a="completed", run_b="completed")

    drain = await loadlib.settle_by_sweep(
        desk, attempts, grace=30, interval=30, poll=5, sleep=sleep, clock=clock.monotonic
    )

    assert [a.outcome for a in attempts] == ["completed"] * 3
    assert attempts[0].recovered_by_sweep and attempts[1].recovered_by_sweep
    assert not attempts[2].recovered_by_sweep
    assert drain["recovered_by_sweep"] == 2 and drain["still_waiting"] == []
    assert 40 <= drain["waited_seconds"] < 60


@pytest.mark.asyncio
async def test_a_run_nothing_completes_stays_not_completed_after_grace_plus_one_interval():
    clock = Clock()
    attempts = [_attempt("run_a")]
    desk = FakeDesk({"run_a": "awaiting_approval"})

    drain = await loadlib.settle_by_sweep(
        desk, attempts, grace=30, interval=30, poll=5, sleep=clock.sleep, clock=clock.monotonic
    )

    assert attempts[0].outcome == "not_completed"
    assert drain["still_waiting"] == ["run_a"]
    assert 60 <= drain["waited_seconds"] <= 65, "gives up after grace plus one interval"


@pytest.mark.asyncio
async def test_a_run_that_ended_without_completing_is_not_waited_for():
    clock = Clock()
    attempts = [_attempt("run_a")]
    desk = FakeDesk({"run_a": "failed"})

    drain = await loadlib.settle_by_sweep(
        desk, attempts, grace=30, interval=30, poll=5, sleep=clock.sleep, clock=clock.monotonic
    )

    assert attempts[0].outcome == "not_completed" and drain["waited_seconds"] == 0
