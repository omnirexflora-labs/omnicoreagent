import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.omnidaemon.result_store import ResultStore, RedisResultStore

def test_resultstore_cannot_instantiate():
    with pytest.raises(TypeError):
        ResultStore()

def test_resultstore_requires_methods():
    # DummyStore missing required methods should raise TypeError
    class DummyStore(ResultStore):
        pass
    with pytest.raises(TypeError):
        DummyStore()

@pytest.mark.asyncio
async def test_redisresultstore_connect_called_once(monkeypatch):
    # Patch aioredis.from_url to return a mock redis client
    mock_redis_client = AsyncMock()
    with patch("src.omnidaemon.result_store.aioredis.from_url", AsyncMock(return_value=mock_redis_client)):
        store = RedisResultStore("redis://localhost")
        await store.connect()
        # Should set _redis_client
        assert store._redis_client is mock_redis_client

@pytest.mark.asyncio
async def test_redisresultstore_save_and_get_result(monkeypatch):
    # Patch aioredis.from_url to return a mock redis client
    mock_redis_client = AsyncMock()
    # Simulate setex and get
    mock_redis_client.setex = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value='{"foo": "bar"}')
    with patch("src.omnidaemon.result_store.aioredis.from_url", AsyncMock(return_value=mock_redis_client)):
        store = RedisResultStore("redis://localhost")
        # Save result
        await store.save_result("task1", {"foo": "bar"})
        mock_redis_client.setex.assert_awaited_once()
        # Get result
        result = await store.get_result("task1")
        mock_redis_client.get.assert_awaited_once()
        assert result == {"foo": "bar"}

@pytest.mark.asyncio
async def test_redisresultstore_get_result_none(monkeypatch):
    # Patch aioredis.from_url to return a mock redis client
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=None)
    with patch("src.omnidaemon.result_store.aioredis.from_url", AsyncMock(return_value=mock_redis_client)):
        store = RedisResultStore("redis://localhost")
        result = await store.get_result("missing_task")
        assert result is None

@pytest.mark.asyncio
async def test_redisresultstore_save_result_connects_if_needed(monkeypatch):
    # Patch aioredis.from_url to return a mock redis client
    mock_redis_client = AsyncMock()
    mock_redis_client.setex = AsyncMock()
    with patch("src.omnidaemon.result_store.aioredis.from_url", AsyncMock(return_value=mock_redis_client)):
        store = RedisResultStore("redis://localhost")
        # _redis_client is None, so should connect
        await store.save_result("task2", {"baz": "qux"})
        assert store._redis_client is mock_redis_client
        mock_redis_client.setex.assert_awaited_once()

@pytest.mark.asyncio
async def test_redisresultstore_get_result_connects_if_needed(monkeypatch):
    # Patch aioredis.from_url to return a mock redis client
    mock_redis_client = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value='{"hello": "world"}')
    with patch("src.omnidaemon.result_store.aioredis.from_url", AsyncMock(return_value=mock_redis_client)):
        store = RedisResultStore("redis://localhost")
        # _redis_client is None, so should connect
        result = await store.get_result("task3")
        assert store._redis_client is mock_redis_client
        mock_redis_client.get.assert_awaited_once()
        assert result == {"hello": "world"}

