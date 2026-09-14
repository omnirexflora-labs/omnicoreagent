"""Test-only routing of all agent connections through real LiteLLM requests.

The process-scoped patch includes dynamically constructed children. It changes no
production defaults and forbids the direct OpenAI SDK adapter during validation.
"""

from contextlib import contextmanager
import inspect
from unittest.mock import patch

from omnicoreagent.core.llm import LLMConnection, _get_litellm
from omnicoreagent.core.model_stream import ModelStreamAssembler


@contextmanager
def use_litellm():
    counters = {"complete_requests": 0, "stream_requests": 0, "closed_streams": 0}
    litellm = _get_litellm()
    litellm.suppress_debug_info = True

    def params(connection, messages, tools):
        values = connection._completion_params(messages, tools)
        if connection.model_config["provider"].lower() != "openai":
            raise ValueError("This validation adapter targets OpenAI only")
        values["model"] = "openai/" + connection.model_config["model"]
        values.update(api_key=connection.llm_api_key, drop_params=False, num_retries=0)
        return values

    async def complete(connection, messages, tools=None):
        counters["complete_requests"] += 1
        return await litellm.acompletion(**params(connection, messages, tools))

    def complete_sync(connection, messages, tools=None):
        counters["complete_requests"] += 1
        return litellm.completion(**params(connection, messages, tools))

    async def stream(connection, messages, tools=None):
        counters["stream_requests"] += 1
        values = params(connection, messages, tools)
        values.update(stream=True, stream_options={"include_usage": True})
        upstream = await litellm.acompletion(**values)
        assembler = ModelStreamAssembler()
        try:
            async for chunk in upstream:
                for event in assembler.feed(chunk):
                    yield event
            yield {"type": "turn_complete", "turn": assembler.finish()}
        finally:
            close = getattr(upstream, "aclose", None) or getattr(
                upstream, "close", None
            )
            if close is None:
                raise RuntimeError("LiteLLM stream has no close method")
            result = close()
            if inspect.isawaitable(result):
                await result
            counters["closed_streams"] += 1

    def forbidden_sdk():
        raise AssertionError("Validation bypassed LiteLLM for the direct SDK adapter")

    with (
        patch.object(LLMConnection, "llm_call", complete),
        patch.object(LLMConnection, "llm_call_sync", complete_sync),
        patch.object(LLMConnection, "llm_stream", stream),
        patch("omnicoreagent.core.llm._get_openai", forbidden_sdk),
    ):
        yield counters
