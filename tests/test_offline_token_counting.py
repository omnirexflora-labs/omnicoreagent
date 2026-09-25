"""A run does not fail because a tokenizer could not be downloaded.

A Harbor task that allowed the agent to reach only its model's host failed
every run with "Model encountered an error (SSLError)" — and the model had
never been called. Counting the context's tokens made tiktoken download its
encoding from openaipublic.blob.core.windows.net, the task's network policy cut
that connection, and the failure was reported as the model's, with nothing
saying which host or why.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.model_protocol import ModelTurn
from omnicoreagent.core.runtime.omnicore_agent import OmniCoreAgent
from omnicoreagent.core.summarizer import tokenizer
from omnicoreagent.core.token_usage import Usage


class _Cut(OSError):
    """What a download cut by a network policy looks like to tiktoken."""


@pytest.fixture
def tiktoken_cannot_download(monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")

    def cut(*args, **kwargs):
        raise _Cut("connection to openaipublic.blob.core.windows.net was reset")

    monkeypatch.setattr(tiktoken, "encoding_for_model", cut)
    monkeypatch.setattr(tiktoken, "get_encoding", cut)
    tokenizer.get_encoding.cache_clear()
    yield
    tokenizer.get_encoding.cache_clear()


def test_counting_falls_back_to_an_estimate_when_the_encoding_cannot_load(
    tiktoken_cannot_download, caplog
):
    count = tokenizer.count_tokens("one two three four five six seven eight nine ten")

    assert count == tokenizer.estimate_tokens_simple(
        "one two three four five six seven eight nine ten"
    )
    assert "openaipublic.blob.core.windows.net" in caplog.text, "the reason is logged"


def test_truncating_falls_back_too(tiktoken_cannot_download):
    text = " ".join(["word"] * 100)

    truncated = tokenizer.truncate_text_to_tokens(text, 13)

    assert 0 < tokenizer.estimate_tokens_simple(truncated) <= 13


class _Model:
    def estimate_cost(self, usage):
        return None

    async def llm_call(self, messages, tools=None, **kwargs):
        return ModelTurn(
            content="hello",
            finish_reason="stop",
            usage=Usage(requests=1, request_tokens=5, response_tokens=1, total_tokens=6),
        )


@pytest.mark.asyncio
async def test_a_run_completes_when_the_encoding_cannot_be_downloaded(
    tiktoken_cannot_download,
):
    agent = OmniCoreAgent(
        name="offline",
        system_instruction="Hi.",
        model_config={"provider": "openai", "model": "gpt-5.6-terra", "api_key": "k"},
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = _Model()
    try:
        result = await agent.run("say hi", session_id="offline")
    finally:
        await agent.cleanup()

    assert result["response"] == "hello"


class _FailingModel(_Model):
    async def llm_call(self, messages, tools=None, **kwargs):
        raise ConnectionError("TLS handshake with api.example-llm.test was reset by peer")


@pytest.mark.asyncio
async def test_a_failed_model_call_says_what_failed_not_only_its_type():
    agent = OmniCoreAgent(
        name="failing",
        system_instruction="Hi.",
        model_config={"provider": "openai", "model": "gpt-5.6-terra", "api_key": "k"},
        agent_config={"guardrail_mode": "off", "enable_workspace_files": False},
    )
    await agent.initialize()
    agent.llm_connection = _FailingModel()
    try:
        result = await agent.run("say hi", session_id="failing")
    finally:
        await agent.cleanup()

    assert "ConnectionError" in result["response"]
    assert "api.example-llm.test was reset by peer" in result["response"]
