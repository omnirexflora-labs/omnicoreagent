"""A fake model provider that speaks the OpenAI chat completions API.

Load and fault tests need a model that costs nothing, answers the same way
every time, and fails on command. This one answers a support chat from a
script (``scenario.py``), after a delay, and can inject the faults a real
provider produces: 429 with ``Retry-After``, 500, a request that hangs past
the client's timeout, and a stream that arrives slowly.

Settings come from the environment at start and from ``POST /_control``
while running. ``GET /_stats`` counts what was served.

Routes:
    POST /v1/chat/completions   streaming and not
    GET  /v1/models
    POST /_control              change settings: {"latency_min": 0.8, "rate_429": 0.1, ...}
    GET  /_stats                counters; ``POST /_control {"reset": true}`` zeroes them
    GET  /_timings              one entry per request: arrival, conversation, status, Retry-After
    GET  /health
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import time
import uuid
from collections import Counter, deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .scenario import CLOCK, _text, decide

# Each setting, its environment variable, and its type. The same names are the
# keys of ``POST /_control``.
SETTINGS = {
    "latency_min": ("FAKE_LATENCY_MIN", float, 0.0),
    "latency_max": ("FAKE_LATENCY_MAX", float, 0.0),
    "rate_429": ("FAKE_RATE_429", float, 0.0),
    "rate_500": ("FAKE_RATE_500", float, 0.0),
    "rate_timeout": ("FAKE_RATE_TIMEOUT", float, 0.0),
    "rate_slow_stream": ("FAKE_RATE_SLOW_STREAM", float, 0.0),
    "retry_after": ("FAKE_RETRY_AFTER", float, 1.0),
    # How long a "timeout" request hangs: it must outlast the client's timeout.
    "hang_seconds": ("FAKE_HANG_SECONDS", float, 120.0),
    # The pause between two chunks of a slow stream.
    "slow_stream_delay": ("FAKE_SLOW_STREAM_DELAY", float, 0.5),
    "seed": ("FAKE_SEED", int, None),
}


def _env(name: str, kind, default):
    raw = os.environ.get(name)
    return kind(raw) if raw not in (None, "") else default


class Provider:
    """The settings, the random source, and the counters of one server."""

    def __init__(self) -> None:
        self.settings = {key: _env(*spec) for key, spec in SETTINGS.items()}
        self.random = random.Random(self.settings["seed"])
        self.reset()

    def reset(self) -> None:
        self.started = time.time()
        self.requests = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.faults: Counter = Counter()
        self.steps: Counter = Counter()
        self.paths: Counter = Counter()
        self.statuses: Counter = Counter()
        # One entry per request, newest last, bounded: what a chaos check reads
        # to ask whether a client waited as long as ``Retry-After`` said.
        self.timings: deque = deque(maxlen=50000)
        self.streams = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def configure(self, changes: dict) -> None:
        for key, value in changes.items():
            if key not in SETTINGS:
                raise ValueError(f"unknown setting: {key}")
            self.settings[key] = None if value is None else SETTINGS[key][1](value)
        if "seed" in changes:
            self.random = random.Random(self.settings["seed"])

    def fault(self) -> str | None:
        """One draw decides the request's fate, so the rates add up."""
        draw = self.random.random()
        edge = 0.0
        for kind in ("429", "500", "timeout"):
            edge += self.settings[f"rate_{kind}"]
            if draw < edge:
                return kind
        return None

    def latency(self) -> float:
        low, high = self.settings["latency_min"], self.settings["latency_max"]
        return self.random.uniform(low, max(low, high))


def _conversation(messages: list[dict]) -> str:
    """A short key for the conversation a request belongs to.

    The first user message names it (the harness puts a unique tag in each
    one), so two requests of one run share a key, and so does a retry.
    """
    first = next((m for m in messages if m.get("role") == "user"), None)
    text = CLOCK.sub("", _text(first.get("content"))) if first else ""
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _tokens(value) -> int:
    # About four characters to a token: close enough for budgets and meters.
    return max(1, len(json.dumps(value, default=str)) // 4)


def _error(status: int, kind: str, message: str, headers: dict | None = None) -> JSONResponse:
    body = {"error": {"message": message, "type": kind, "param": None, "code": kind}}
    return JSONResponse(body, status_code=status, headers=headers)


def _sse(payload) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n".encode()


def create_app() -> Starlette:
    provider = Provider()

    async def chat_completions(request: Request):
        provider.requests += 1
        provider.paths[request.url.path] += 1
        provider.in_flight += 1
        provider.max_in_flight = max(provider.max_in_flight, provider.in_flight)
        try:
            return await _serve(request)
        finally:
            provider.in_flight -= 1

    async def _serve(request: Request):
        try:
            body = await request.json()
        except ValueError:
            provider.statuses[400] += 1
            return _error(400, "invalid_request_error", "The body is not JSON.")

        fault = provider.fault()
        timing = {
            "t_in": time.time(), "conv": _conversation(body.get("messages") or []),
            "fault": fault, "status": None, "retry_after": None, "t_out": None,
        }
        provider.timings.append(timing)
        await asyncio.sleep(provider.latency())
        if fault == "429":
            provider.faults["429"] += 1
            provider.statuses[429] += 1
            retry_after = provider.settings["retry_after"]
            timing.update(status=429, retry_after=retry_after, t_out=time.time())
            return _error(
                429, "rate_limit_exceeded", "Rate limit reached. Try again later.",
                {"Retry-After": f"{retry_after:g}", "retry-after-ms": str(int(retry_after * 1000))},
            )
        if fault == "500":
            provider.faults["500"] += 1
            provider.statuses[500] += 1
            timing.update(status=500, t_out=time.time())
            return _error(500, "server_error", "The server had an error while processing your request.")
        if fault == "timeout":
            provider.faults["timeout"] += 1
            timing.update(status="hang")
            await asyncio.sleep(provider.settings["hang_seconds"])

        messages = body.get("messages") or []
        tools = {t["function"]["name"] for t in body.get("tools") or [] if t.get("type") == "function"}
        step = decide(messages, tools)
        provider.steps[step.tool_name or "answer"] += 1

        model = body.get("model", "fake-model")
        call_id = f"call_{uuid.uuid4().hex[:24]}"
        message = {"role": "assistant", "content": step.text}
        if step.tool_name:
            message["tool_calls"] = [{
                "id": call_id, "type": "function",
                "function": {"name": step.tool_name, "arguments": json.dumps(step.arguments)},
            }]
        usage = {
            "prompt_tokens": _tokens(messages) + _tokens(body.get("tools") or []),
            "completion_tokens": _tokens(message) + 8,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        if timing["status"] is None:
            timing.update(status=200)
        timing["t_out"] = time.time()
        provider.prompt_tokens += usage["prompt_tokens"]
        provider.completion_tokens += usage["completion_tokens"]
        finish = "tool_calls" if step.tool_name else "stop"
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        if not body.get("stream"):
            provider.statuses[200] += 1
            return JSONResponse({
                "id": completion_id, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            })

        provider.streams += 1
        provider.statuses[200] += 1
        slow = provider.random.random() < provider.settings["rate_slow_stream"]
        if slow:
            provider.faults["slow_stream"] += 1
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        def chunk(delta, finish_reason=None, **extra):
            return {
                "id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                **extra,
            }

        async def events():
            yield _sse(chunk({"role": "assistant", "content": ""}))
            if step.tool_name:
                arguments = message["tool_calls"][0]["function"]["arguments"]
                yield _sse(chunk({"tool_calls": [{
                    "index": 0, "id": call_id, "type": "function",
                    "function": {"name": step.tool_name, "arguments": ""},
                }]}))
                pieces = [arguments[i:i + 12] for i in range(0, len(arguments), 12)]
                for piece in pieces:
                    if slow:
                        await asyncio.sleep(provider.settings["slow_stream_delay"])
                    yield _sse(chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}))
            else:
                words = step.text.split(" ")
                for i, word in enumerate(words):
                    if slow:
                        await asyncio.sleep(provider.settings["slow_stream_delay"])
                    yield _sse(chunk({"content": word + (" " if i < len(words) - 1 else "")}))
            yield _sse(chunk({}, finish))
            if include_usage:
                yield _sse({
                    "id": completion_id, "object": "chat.completion.chunk", "created": created,
                    "model": model, "choices": [], "usage": usage,
                })
            yield _sse("[DONE]")

        return StreamingResponse(events(), media_type="text/event-stream")

    async def models(request: Request):
        return JSONResponse({"object": "list", "data": [{"id": "fake-model", "object": "model", "owned_by": "fake"}]})

    async def control(request: Request):
        changes = await request.json()
        if changes.pop("reset", False):
            provider.reset()
        try:
            provider.configure(changes)
        except (ValueError, TypeError) as error:
            return JSONResponse({"detail": str(error)}, status_code=422)
        return JSONResponse({"settings": provider.settings})

    async def stats(request: Request):
        return JSONResponse({
            "uptime_seconds": round(time.time() - provider.started, 1),
            "requests": provider.requests,
            "in_flight": provider.in_flight,
            "max_in_flight": provider.max_in_flight,
            "streams": provider.streams,
            "by_path": dict(provider.paths),
            "by_status": {str(k): v for k, v in provider.statuses.items()},
            "faults": dict(provider.faults),
            "steps": dict(provider.steps),
            "prompt_tokens": provider.prompt_tokens,
            "completion_tokens": provider.completion_tokens,
            "settings": provider.settings,
        })

    async def timings(request: Request):
        # ``?since=<epoch seconds>`` keeps a long run's answer small.
        since = float(request.query_params.get("since", 0))
        return JSONResponse({"timings": [t for t in provider.timings if t["t_in"] >= since]})

    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    return Starlette(routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/models", models),
        Route("/_control", control, methods=["POST"]),
        Route("/_stats", stats),
        Route("/_timings", timings),
        Route("/health", health),
    ])


app = create_app()
