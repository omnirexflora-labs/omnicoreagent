"""The token counter's encoding is loaded with the model client, off the loop.

The 0.5.0rc2 gate: tiktoken's encoding was loaded on the event loop at a
run's first count (the context check before its first model call): 2.4-3.2 s
frozen on a loaded host, and on one without the tiktoken cache a download
over the network, on the loop.
"""

from __future__ import annotations

import threading

import pytest

from omnicoreagent.core import llm as llm_module
from omnicoreagent.core.summarizer import tokenizer


@pytest.mark.asyncio
async def test_loading_the_model_client_loads_the_encoding_on_a_thread(monkeypatch):
    threads: list = []

    def fake_encoding(model: str = "gpt-4"):
        threads.append(threading.current_thread().name)
        return None

    monkeypatch.setattr(llm_module, "_get_litellm", lambda: object())
    monkeypatch.setattr(llm_module, "_LITELLM_LOADED", False)
    monkeypatch.setattr(llm_module, "_ENCODING_LOADED", False, raising=False)
    monkeypatch.setattr(tokenizer, "get_encoding", fake_encoding)

    await llm_module.load_model_client()
    await llm_module.load_model_client()

    assert len(threads) == 1, "loaded once"
    assert threads[0] != threading.current_thread().name, "not on the event loop's thread"
