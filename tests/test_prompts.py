from unittest.mock import AsyncMock, MagicMock

import pytest

from omnicoreagent.mcp_clients_connection.prompts import (
    find_prompt_server,
    get_prompt,
    get_prompt_with_react_agent,
    list_prompts,
)


@pytest.mark.asyncio
async def test_list_prompts_all_connected():
    sessions = {
        "server1": {"connected": True, "session": AsyncMock()},
        "server2": {"connected": True, "session": AsyncMock()},
    }
    sessions["server1"]["session"].list_prompts.return_value = MagicMock(
        prompts=[{"name": "a"}]
    )
    sessions["server2"]["session"].list_prompts.return_value = MagicMock(
        prompts=[{"name": "b"}]
    )

    result = await list_prompts(["server1", "server2"], sessions)
    assert len(result) == 2
    assert {"name": "a"} in result
    assert {"name": "b"} in result


@pytest.mark.asyncio
async def test_list_prompts_with_error():
    sessions = {
        "server1": {"connected": True, "session": AsyncMock()},
        "server2": {"connected": True, "session": AsyncMock()},
    }
    sessions["server1"]["session"].list_prompts.side_effect = Exception("boom")
    sessions["server2"]["session"].list_prompts.return_value = MagicMock(
        prompts=[{"name": "b"}]
    )

    result = await list_prompts(["server1", "server2"], sessions)
    assert len(result) == 1
    assert {"name": "b"} in result


@pytest.mark.asyncio
async def test_find_prompt_server_found():
    prompts = {
        "server1": [{"name": "alpha"}],
        "server2": [{"name": "beta"}],
    }
    server_name, found = await find_prompt_server("beta", prompts)
    assert server_name == "server2"
    assert found is True


@pytest.mark.asyncio
async def test_find_prompt_server_not_found():
    prompts = {
        "server1": [{"name": "alpha"}],
    }
    server_name, found = await find_prompt_server("gamma", prompts)
    assert not found
    assert server_name == ""


@pytest.mark.asyncio
async def test_get_prompt_success():
    mock_session = AsyncMock()
    mock_session.get_prompt.return_value = MagicMock(
        messages=[MagicMock(role="user", content=MagicMock(text="prompt response"))]
    )

    sessions = {"server1": {"connected": True, "session": mock_session}}
    available_prompts = {"server1": [{"name": "test"}]}

    async def mock_add(*args, **kwargs):
        return {}

    result = await get_prompt(
        sessions,
        system_prompt="system",
        add_message_to_history=mock_add,
        llm_call=lambda x: x,
        debug=True,
        available_prompts=available_prompts,
        name="test",
    )
    assert result == "prompt response"


@pytest.mark.asyncio
async def test_get_prompt_with_react_agent_success():
    mock_session = AsyncMock()
    mock_session.get_prompt.return_value = MagicMock(
        messages=[MagicMock(role="user", content=MagicMock(text="react prompt"))]
    )

    sessions = {"server1": {"connected": True, "session": mock_session}}
    available_prompts = {"server1": [{"name": "react"}]}

    async def mock_add(*args, **kwargs):
        return {}

    result = await get_prompt_with_react_agent(
        sessions,
        system_prompt="sys",
        add_message_to_history=mock_add,
        debug=False,
        available_prompts=available_prompts,
        name="react",
    )
    assert result == "react prompt"


@pytest.mark.asyncio
async def test_get_prompt_with_error_handling():
    mock_session = AsyncMock()
    mock_session.get_prompt.side_effect = Exception("network failure")

    sessions = {"server1": {"connected": True, "session": mock_session}}
    available_prompts = {"server1": [{"name": "fail"}]}

    async def mock_add(*args, **kwargs):
        return {}

    result = await get_prompt_with_react_agent(
        sessions,
        system_prompt="sys",
        add_message_to_history=mock_add,
        debug=True,
        available_prompts=available_prompts,
        name="fail",
    )
    assert "Error getting prompt" in result
