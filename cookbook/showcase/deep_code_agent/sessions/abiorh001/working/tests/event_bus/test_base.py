import sys
import os
import pytest
import asyncio
import types
import importlib.util

# Dynamically load base.py from its actual location
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../src/omnidaemon/event_bus/base.py"))
if not os.path.exists(base_path):
    # Try workspace-rooted path
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/omnidaemon/event_bus/base.py"))
spec = importlib.util.spec_from_file_location("base", base_path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

def test_abstract_method_enforcement():
    class DummyBus(base.BaseEventBus):
        pass

    with pytest.raises(TypeError):
        DummyBus()

def test_minimal_subclass_instantiation():
    class MinimalBus(base.BaseEventBus):
        async def publish(self, event):
            pass

        async def subscribe(self, callback):
            pass

        async def unsubscribe(self, callback):
            pass

        async def close(self):
            pass

        async def connect(self):
            pass

        def get_consumers(self):
            return []

    bus = MinimalBus()
    assert isinstance(bus, base.BaseEventBus)

def test_method_signatures():
    import inspect

    class MinimalBus(base.BaseEventBus):
        async def publish(self, event):
            pass

        async def subscribe(self, callback):
            pass

        async def unsubscribe(self, callback):
            pass

        async def close(self):
            pass

        async def connect(self):
            pass

        def get_consumers(self):
            return []

    bus = MinimalBus()
    for method_name in ['publish', 'subscribe', 'unsubscribe', 'close', 'connect']:
        method = getattr(bus, method_name)
        assert inspect.iscoroutinefunction(method)
    # get_consumers should be a regular function
    assert callable(bus.get_consumers)
    assert not inspect.iscoroutinefunction(bus.get_consumers)
