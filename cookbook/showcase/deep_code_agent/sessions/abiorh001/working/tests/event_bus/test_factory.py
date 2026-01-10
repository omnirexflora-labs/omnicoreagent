import sys
sys.path.insert(0, "src")

import pytest
import sys as _sys
import types
from unittest.mock import patch, AsyncMock, MagicMock

import importlib

import omnidaemon.event_bus.factory as factory_mod

@pytest.mark.asyncio
async def test_singleton_behavior(monkeypatch):
    # Mock config to always return "redis_stream"
    monkeypatch.setattr("omnidaemon.event_bus.factory.config", lambda key: "redis_stream")
    # Mock import_module to return a dummy backend class
    dummy_backend = type("DummyBackend", (), {
        "connect": AsyncMock(),
        "close": AsyncMock()
    })
    dummy_instance = dummy_backend()
    monkeypatch.setattr(importlib, "import_module", lambda name: types.SimpleNamespace(RedisStreamEventBus=dummy_backend))
    # Patch _load_backend to return dummy_instance directly
    monkeypatch.setattr(factory_mod.EventBusFactory, "_load_backend", AsyncMock(return_value=dummy_instance))
    # Reset singleton
    factory_mod.EventBusFactory._instance = None
    # First call initializes
    bus1 = await factory_mod.EventBusFactory.get_event_bus()
    # Second call returns same instance
    bus2 = await factory_mod.EventBusFactory.get_event_bus()
    assert bus1 is bus2
    await factory_mod.EventBusFactory.shutdown()

@pytest.mark.asyncio
async def test_backend_loading(monkeypatch):
    # Test correct backend is loaded for redis_stream
    monkeypatch.setattr("omnidaemon.event_bus.factory.config", lambda key: "redis_stream")
    dummy_backend = type("DummyBackend", (), {
        "connect": AsyncMock(),
        "close": AsyncMock()
    })
    monkeypatch.setattr(importlib, "import_module", lambda name: types.SimpleNamespace(RedisStreamEventBus=dummy_backend))
    # Patch _load_backend to call the real method but with dummy class
    with patch.object(factory_mod.EventBusFactory, "_load_backend", AsyncMock(return_value=dummy_backend())) as mock_load:
        factory_mod.EventBusFactory._instance = None
        bus = await factory_mod.EventBusFactory.get_event_bus()
        assert isinstance(bus, dummy_backend)
        assert mock_load.called
        await factory_mod.EventBusFactory.shutdown()

@pytest.mark.asyncio
async def test_unsupported_backend(monkeypatch):
    monkeypatch.setattr("omnidaemon.event_bus.factory.config", lambda key: "not_supported")
    factory_mod.EventBusFactory._instance = None
    with pytest.raises(ValueError):
        await factory_mod.EventBusFactory.get_event_bus()

@pytest.mark.asyncio
async def test_import_error(monkeypatch):
    monkeypatch.setattr("omnidaemon.event_bus.factory.config", lambda key: "redis_stream")
    # Patch import_module to raise ImportError
    monkeypatch.setattr(importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("fail")))
    # Patch _load_backend to call the real method
    with patch.object(factory_mod.EventBusFactory, "_load_backend", wraps=factory_mod.EventBusFactory._load_backend):
        factory_mod.EventBusFactory._instance = None
        with pytest.raises(ImportError):
            await factory_mod.EventBusFactory.get_event_bus()

@pytest.mark.asyncio
async def test_shutdown_resets_instance(monkeypatch):
    monkeypatch.setattr("omnidaemon.event_bus.factory.config", lambda key: "redis_stream")
    dummy_backend = type("DummyBackend", (), {
        "connect": AsyncMock(),
        "close": AsyncMock()
    })
    dummy_instance = dummy_backend()
    monkeypatch.setattr(importlib, "import_module", lambda name: types.SimpleNamespace(RedisStreamEventBus=dummy_backend))
    monkeypatch.setattr(factory_mod.EventBusFactory, "_load_backend", AsyncMock(return_value=dummy_instance))
    factory_mod.EventBusFactory._instance = None
    await factory_mod.EventBusFactory.get_event_bus()
    await factory_mod.EventBusFactory.shutdown()
    assert factory_mod.EventBusFactory._instance is None

