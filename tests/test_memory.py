import pytest
from omnicoreagent.core.memory_store.in_memory import InMemoryStore


@pytest.mark.asyncio
class TestInMemoryStore:
    @pytest.fixture
    def memory(self):
        return InMemoryStore()

    async def test_store_and_get_messages(self, memory):
        await memory.store_message(
            "user", "Hello", {"agent_name": "agent1"}, "session1"
        )
        await memory.store_message(
            "assistant", "Hi", {"agent_name": "agent1"}, "session1"
        )

        messages = await memory.get_messages("session1", "agent1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    async def test_get_empty_messages(self, memory):
        messages = await memory.get_messages("session1", "unknown_agent")
        assert messages == []

    async def test_clear_memory_specific_agent(self, memory):
        # Clear specific agent in a session
        await memory.store_message(
            "user", "Hello", {"agent_name": "agent1"}, "session1"
        )
        await memory.store_message("user", "Hi", {"agent_name": "agent2"}, "session1")

        await memory.clear_memory("session1", "agent1")
        assert await memory.get_messages("session1", "agent1") == []
        messages = await memory.get_messages("session1", "agent2")
        assert len(messages) == 1

    async def test_clear_memory_all(self, memory):
        await memory.store_message(
            "user", "Hello", {"agent_name": "agent1"}, "session1"
        )

        await memory.clear_memory()
        assert await memory.get_messages("session1") == []

    async def test_truncate_message_history(self, memory):
        memory.set_memory_config("token_budget", value=5)
        long_message = "word " * 50
        await memory.store_message(
            "user", long_message, {"agent_name": "agent1"}, "session1"
        )

        messages = await memory.get_messages("session1", "agent1")
        # Logic pops messages until under limit. Since 1 message is > 5 tokens, it pops it.
        assert len(messages) == 0
