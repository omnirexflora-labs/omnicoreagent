"""Which provider errors are retried, and how long the retry waits.

The support desk chaos run (2026-10-07) found two faults. Twelve provider
500s failed twelve runs, because only errors whose text said "rate limit" or
"timeout" were retried. And a 429 with ``Retry-After: 3`` was retried after
1.1 to 1.3 seconds, because the backoff never read the header. These tests
use the app's fake provider through a real LiteLLM client, so the status
codes and headers are the ones LiteLLM produces.
"""

from __future__ import annotations

import sys
import threading
import time
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fake_provider import running_fake_provider  # noqa: E402

from omnicoreagent.core import llm as llm_module  # noqa: E402
from omnicoreagent.core.llm import LLMConnection, _is_retryable, _retry_after  # noqa: E402
from omnicoreagent.core.runtime.deadline import stop_after  # noqa: E402


class _Status(Exception):
    def __init__(self, status, message="boom", headers=None):
        super().__init__(message)
        self.status_code = status
        self.litellm_response_headers = headers or {}


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 408])
def test_transient_statuses_are_retried_whatever_the_text_says(status):
    assert _is_retryable(_Status(status, "The server had an error"))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_other_client_errors_fail_fast_even_when_the_text_says_timeout(status):
    assert not _is_retryable(_Status(status, "the request timeout parameter is invalid"))


def test_an_out_of_credits_429_still_fails_fast():
    assert not _is_retryable(_Status(429, "insufficient_quota: you exceeded your current quota"))


def test_retry_after_reads_seconds_milliseconds_and_http_dates():
    assert _retry_after(_Status(429, headers={"retry-after": "3"})) == 3
    assert _retry_after(_Status(429, headers={"Retry-After": "2.5"})) == 2.5
    assert _retry_after(_Status(429, headers={"retry-after-ms": "1500"})) == 1.5
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert 25 < _retry_after(_Status(429, headers={"retry-after": format_datetime(when, usegmt=True)})) <= 30
    assert _retry_after(_Status(429, headers={"retry-after": "soon"})) is None
    assert _retry_after(_Status(500)) is None
    assert _retry_after(RuntimeError("no headers at all")) is None


def test_a_retry_after_in_the_past_means_no_wait():
    past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=60), usegmt=True)
    assert _retry_after(_Status(429, headers={"retry-after": past})) == 0


def test_the_delay_honours_retry_after_but_not_beyond_the_cap():
    error = _Status(429, headers={"retry-after": "3"})
    delays = [llm_module._retry_delay(error, 0, 3, 1, 30, 2) for _ in range(20)]
    assert all(3 <= delay <= 3.76 for delay in delays)
    huge = _Status(429, headers={"retry-after": "100000"})
    assert llm_module._retry_delay(huge, 0, 3, 1, 30, 2) <= llm_module.MAX_RETRY_AFTER_SECONDS * 1.25 + 0.01
    assert llm_module._retry_delay(_Status(500), 1, 3, 1, 30, 2) >= 2


@pytest.mark.asyncio
async def test_a_wait_past_the_runs_deadline_gives_up_at_once(monkeypatch):
    calls = []

    @llm_module.retry_with_backoff(max_retries=3, base_delay=1)
    async def call():
        calls.append(time.monotonic())
        raise _Status(429, headers={"retry-after": "30"})

    started = time.monotonic()
    async with stop_after(5):
        with pytest.raises(_Status):
            await call()
    assert len(calls) == 1 and time.monotonic() - started < 1


def _connection(url):
    return LLMConnection(
        {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "fake", "base_url": f"{url}/v1"}
    )


def _fault_for_the_first_request_only(url, **fault):
    """Inject ``fault`` until the first request arrives, then clear it."""
    httpx.post(f"{url}/_control", json={"reset": True, **fault})

    def clear():
        for _ in range(500):
            if _timings(url):
                httpx.post(f"{url}/_control", json={key: 0 for key in fault if key.startswith("rate_")})
                return
            time.sleep(0.01)

    thread = threading.Thread(target=clear, daemon=True)
    thread.start()
    return thread


def _timings(url):
    return httpx.get(f"{url}/_timings").json()["timings"]


@pytest.mark.asyncio
async def test_a_provider_500_is_retried_and_then_succeeds():
    with running_fake_provider() as url:
        thread = _fault_for_the_first_request_only(url, rate_500=1.0)
        response = await _connection(url).llm_call([{"role": "user", "content": "hello"}])
        thread.join(timeout=5)
        statuses = [entry["status"] for entry in _timings(url)]
    assert statuses == [500, 200]
    assert response.choices[0].message.content


@pytest.mark.asyncio
async def test_a_429_waits_at_least_as_long_as_retry_after_said():
    with running_fake_provider() as url:
        thread = _fault_for_the_first_request_only(url, rate_429=1.0, retry_after=2)
        response = await _connection(url).llm_call([{"role": "user", "content": "hello"}])
        thread.join(timeout=5)
        entries = _timings(url)
    assert [entry["status"] for entry in entries] == [429, 200]
    # The gap between the two arrivals is the whole wait the client chose.
    assert entries[1]["t_in"] - entries[0]["t_out"] >= 2.0
    assert response.choices[0].message.content


@pytest.mark.asyncio
async def test_a_400_is_called_once():
    calls = []

    @llm_module.retry_with_backoff(max_retries=3, base_delay=0)
    async def call():
        calls.append(1)
        raise _Status(400, "bad request: timeout must be a number")

    with pytest.raises(_Status):
        await call()
    assert len(calls) == 1
