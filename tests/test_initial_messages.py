import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omnicoreagent.core.agents.initial_messages import AgentInitialMessagePreparer
from omnicoreagent.core.tools.native_catalog import NativeToolCatalog


@pytest.mark.asyncio
async def test_preparer_uses_catalog_capabilities_and_loads_history():
    builder = SimpleNamespace(build_system_prompt=AsyncMock(return_value="prompt"))
    loader = SimpleNamespace(load=AsyncMock())
    state = SimpleNamespace(messages=[])
    preparer = AgentInitialMessagePreparer(
        message_history_loader=loader, prompt_context_builder=builder
    )
    await preparer.prepare(
        session_state=state,
        system_prompt="base",
        session_id="s",
        message_history=None,
        catalog=NativeToolCatalog(advanced=True),
    )
    loader.load.assert_awaited_once()
    assert builder.build_system_prompt.call_args.kwargs["available_tools"] == {
        "tools_retriever"
    }
    assert state.messages[0].content == "prompt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("store failed"), asyncio.TimeoutError()]
)
async def test_preparer_never_silently_discards_history_failure(failure):
    builder = SimpleNamespace(build_system_prompt=AsyncMock())
    preparer = AgentInitialMessagePreparer(
        message_history_loader=SimpleNamespace(load=AsyncMock(side_effect=failure)),
        prompt_context_builder=builder,
    )
    with pytest.raises(type(failure)):
        await preparer.prepare(
            session_state=SimpleNamespace(messages=[]),
            system_prompt="base",
            session_id="s",
            message_history=None,
            catalog=NativeToolCatalog(),
        )
    builder.build_system_prompt.assert_not_called()
