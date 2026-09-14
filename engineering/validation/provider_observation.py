"""Observe real production LiteLLM requests without replacing agent methods."""

from contextlib import contextmanager
from unittest.mock import patch

from omnicoreagent.core.llm import _get_litellm


@contextmanager
def observe_litellm():
    counters = {"complete_requests": 0, "stream_requests": 0}
    litellm = _get_litellm()
    litellm.suppress_debug_info = True
    original_async = litellm.acompletion
    original_sync = litellm.completion

    async def observed_async(*args, **kwargs):
        key = "stream_requests" if kwargs.get("stream") else "complete_requests"
        counters[key] += 1
        return await original_async(*args, **kwargs)

    def observed_sync(*args, **kwargs):
        counters["complete_requests"] += 1
        return original_sync(*args, **kwargs)

    with (
        patch.object(litellm, "acompletion", observed_async),
        patch.object(litellm, "completion", observed_sync),
    ):
        yield counters
