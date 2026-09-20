"""A parameter the model refuses is dropped, once, on the record.

Found deploying the repository steward: its first model call failed with
"gpt-5.6-terra doesn't support temperature=0.2 while reasoning is active" and
the whole run ended as a provider error. The runtime sends parameters as
given (drop_params=False, on purpose: silent drops hide misconfiguration),
but when the provider names the parameter it refuses, failing the run is the
wrong answer. The call is made again without that parameter, the retry is
recorded with the parameter's name so the trace shows it, and the connection
remembers not to send it again.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from omnicoreagent.core.llm import MODEL_RETRY_OBSERVER, LLMConnection


class UnsupportedParamsError(Exception):
    """Shaped like litellm's: the runtime must not need litellm to recognise it."""


def _connection() -> LLMConnection:
    return LLMConnection(
        {"provider": "openai", "model": "gpt-5.6-terra", "api_key": "k", "temperature": 0.2, "max_tokens": 50}
    )


@pytest.mark.asyncio
async def test_a_refused_parameter_is_dropped_once_and_recorded():
    seen: list[dict] = []

    async def acompletion(**params):
        seen.append(params)
        if "temperature" in params:
            raise UnsupportedParamsError(
                "litellm.UnsupportedParamsError: gpt-5.6-terra doesn't support temperature=0.2 "
                "while reasoning is active. Only temperature=1 is supported"
            )
        return SimpleNamespace(choices=[], usage=None)

    retries: list[dict] = []
    token = MODEL_RETRY_OBSERVER.set(retries.append)
    try:
        with patch("omnicoreagent.core.llm._get_litellm", return_value=SimpleNamespace(acompletion=acompletion)):
            connection = _connection()
            await connection.llm_call([{"role": "user", "content": "hi"}])
            await connection.llm_call([{"role": "user", "content": "again"}])
    finally:
        MODEL_RETRY_OBSERVER.reset(token)

    assert len(seen) == 3, "first call refused, retried once without the parameter, then one clean call"
    assert "temperature" in seen[0] and "temperature" not in seen[1] and "temperature" not in seen[2]
    [record] = retries
    assert record.get("reason") == "unsupported_parameter" and record.get("dropped") == ["temperature"]


@pytest.mark.asyncio
async def test_a_refusal_that_names_no_parameter_still_fails():
    async def acompletion(**params):
        raise UnsupportedParamsError("litellm.UnsupportedParamsError: this model cannot do that")

    with patch("omnicoreagent.core.llm._get_litellm", return_value=SimpleNamespace(acompletion=acompletion)):
        with pytest.raises(UnsupportedParamsError):
            await _connection().llm_call([{"role": "user", "content": "hi"}])
