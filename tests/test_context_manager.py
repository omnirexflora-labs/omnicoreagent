"""
Comprehensive tests for AgentLoopContextManager.

Tests cover:
- Configuration parsing and validation
- Token counting accuracy
- Sliding window mode
- Token budget mode
- Truncate strategy
- Summarize and truncate strategy
- Edge cases: empty messages, only system, exact threshold
- System prompt preservation
- Recent message preservation
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from omnicoreagent.core.context_manager import (
    AgentLoopContextManager,
    ContextManagementConfig,
    ContextManagementMode,
    ContextManagementStrategy,
)
from omnicoreagent.core.summarizer.tokenizer import count_tokens


# ============================================================================
# Configuration Tests
# ============================================================================

class TestContextManagementConfig:
    """Tests for ContextManagementConfig parsing."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = ContextManagementConfig()
        assert config.enabled is False
        assert config.mode == ContextManagementMode.TOKEN_BUDGET
        assert config.value == 100000
        assert config.threshold_percent == 75
        assert config.strategy == ContextManagementStrategy.TRUNCATE
        assert config.preserve_recent == 4
    
    def test_from_dict_empty(self):
        """Test creating config from empty dict."""
        config = ContextManagementConfig.from_dict({})
        assert config.enabled is False
    
    def test_from_dict_enabled(self):
        """Test creating config from dict with enabled=True."""
        config = ContextManagementConfig.from_dict({
            "enabled": True,
            "mode": "sliding_window",
            "value": 20,
            "threshold_percent": 80,
            "strategy": "summarize_and_truncate",
            "preserve_recent": 6,
        })
        assert config.enabled is True
        assert config.mode == ContextManagementMode.SLIDING_WINDOW
        assert config.value == 20
        assert config.threshold_percent == 80
        assert config.strategy == ContextManagementStrategy.SUMMARIZE_AND_TRUNCATE
        assert config.preserve_recent == 6
    
    def test_from_dict_partial(self):
        """Test creating config from partial dict (should use defaults)."""
        config = ContextManagementConfig.from_dict({
            "enabled": True,
            "value": 50000,
        })
        assert config.enabled is True
        assert config.value == 50000
        assert config.mode == ContextManagementMode.TOKEN_BUDGET  # default
        assert config.preserve_recent == 4  # default
    
    def test_from_dict_none(self):
        """Test creating config from None."""
        config = ContextManagementConfig.from_dict(None)
        assert config.enabled is False


# ============================================================================
# Validation Tests (via AgentConfig in types.py)
# ============================================================================

class TestContextManagementValidation:
    """Tests for context_management validation in AgentConfig."""
    
    def test_preserve_recent_minimum_enforced(self):
        """Test that preserve_recent < 4 raises ValidationError."""
        from omnicoreagent.core.types import AgentConfig
        import pytest
        
        with pytest.raises(Exception) as exc_info:
            AgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                context_management={
                    "enabled": True,
                    "preserve_recent": 2,  # Below minimum of 4
                }
            )
        assert "preserve_recent must be at least 4" in str(exc_info.value)
    
    def test_valid_preserve_recent_accepted(self):
        """Test that preserve_recent >= 4 is accepted."""
        from omnicoreagent.core.types import AgentConfig
        
        config = AgentConfig(
            agent_name="test",
            max_steps=10,
            tool_call_timeout=30,
            context_management={
                "enabled": True,
                "preserve_recent": 4,
            }
        )
        assert config.context_management["preserve_recent"] == 4
    
    def test_invalid_mode_rejected(self):
        """Test that invalid mode raises ValidationError."""
        from omnicoreagent.core.types import AgentConfig
        import pytest
        
        with pytest.raises(Exception) as exc_info:
            AgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                context_management={
                    "enabled": True,
                    "mode": "invalid_mode",
                }
            )
        assert "mode must be one of" in str(exc_info.value)
    
    def test_invalid_strategy_rejected(self):
        """Test that invalid strategy raises ValidationError."""
        from omnicoreagent.core.types import AgentConfig
        import pytest
        
        with pytest.raises(Exception) as exc_info:
            AgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                context_management={
                    "enabled": True,
                    "strategy": "bad_strategy",
                }
            )
        assert "strategy must be one of" in str(exc_info.value)
    
    def test_invalid_threshold_rejected(self):
        """Test that threshold outside 1-100 raises ValidationError."""
        from omnicoreagent.core.types import AgentConfig
        import pytest
        
        with pytest.raises(Exception) as exc_info:
            AgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                context_management={
                    "enabled": True,
                    "threshold_percent": 150,
                }
            )
        assert "threshold_percent must be between 1 and 100" in str(exc_info.value)


# ============================================================================
# Token Counting Tests
# ============================================================================

class TestTokenCounting:
    """Tests for accurate token counting."""
    
    def test_count_tokens_with_dict_messages(self):
        """Test token counting with dict messages."""
        manager = AgentLoopContextManager({"enabled": True})
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing great, thank you!"},
        ]
        
        total = manager.get_current_token_count(messages)
        
        # Verify token count is reasonable (should be > 0)
        assert total > 0
        
        # Verify it matches individual counts
        expected = sum(count_tokens(m["content"]) for m in messages)
        assert total == expected
    
    def test_count_tokens_with_message_objects(self):
        """Test token counting with Message objects."""
        manager = AgentLoopContextManager({"enabled": True})
        
        # Mock Message objects
        class MockMessage:
            def __init__(self, role, content):
                self.role = role
                self.content = content
        
        messages = [
            MockMessage("system", "System prompt here"),
            MockMessage("user", "User message"),
        ]
        
        total = manager.get_current_token_count(messages)
        assert total > 0
    
    def test_count_tokens_empty_messages(self):
        """Test token counting with empty message list."""
        manager = AgentLoopContextManager({"enabled": True})
        total = manager.get_current_token_count([])
        assert total == 0
    
    def test_count_tokens_empty_content(self):
        """Test token counting with empty content."""
        manager = AgentLoopContextManager({"enabled": True})
        messages = [{"role": "user", "content": ""}]
        total = manager.get_current_token_count(messages)
        assert total == 0


# ============================================================================
# Should Trigger Tests
# ============================================================================

class TestShouldTrigger:
    """Tests for threshold triggering logic."""
    
    def test_disabled_never_triggers(self):
        """Test that disabled config never triggers."""
        manager = AgentLoopContextManager({"enabled": False})
        messages = [{"role": "user", "content": "x" * 10000}]
        assert manager.should_trigger(messages) is False
    
    def test_token_budget_below_threshold(self):
        """Test token budget mode below threshold doesn't trigger."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "mode": "token_budget",
            "value": 10000,  # 10k tokens
            "threshold_percent": 75,  # Trigger at 7500
        })
        # Small message, well below threshold
        messages = [{"role": "user", "content": "Hello world"}]
        assert manager.should_trigger(messages) is False
    
    def test_token_budget_above_threshold(self):
        """Test token budget mode above threshold triggers."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "mode": "token_budget",
            "value": 100,  # 100 tokens
            "threshold_percent": 75,  # Trigger at 75
        })
        # Large message to exceed threshold
        messages = [{"role": "user", "content": "word " * 100}]
        assert manager.should_trigger(messages) is True
    
    def test_sliding_window_below_threshold(self):
        """Test sliding window mode below threshold doesn't trigger."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "mode": "sliding_window",
            "value": 10,  # 10 messages
        })
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User1"},
            {"role": "assistant", "content": "Response1"},
        ]
        # Only 2 non-system messages, below 10
        assert manager.should_trigger(messages) is False
    
    def test_sliding_window_above_threshold(self):
        """Test sliding window mode above threshold triggers."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "mode": "sliding_window",
            "value": 5,  # 5 messages
        })
        # Create 7 non-system messages
        messages = [{"role": "system", "content": "System"}]
        for i in range(7):
            messages.append({"role": "user", "content": f"Message {i}"})
        
        assert manager.should_trigger(messages) is True
    
    def test_sliding_window_excludes_system(self):
        """Test sliding window correctly excludes system messages from count."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "mode": "sliding_window",
            "value": 5,
        })
        # 3 system messages + 4 user messages = 4 non-system (below 5)
        messages = [
            {"role": "system", "content": "System 1"},
            {"role": "system", "content": "System 2"},
            {"role": "system", "content": "System 3"},
            {"role": "user", "content": "User 1"},
            {"role": "user", "content": "User 2"},
            {"role": "user", "content": "User 3"},
            {"role": "user", "content": "User 4"},
        ]
        
        count = manager.get_non_system_message_count(messages)
        assert count == 4
        assert manager.should_trigger(messages) is False


# ============================================================================
# Manage Context Tests - Truncate Strategy
# ============================================================================

@pytest.mark.asyncio
class TestManageContextTruncate:
    """Tests for truncate strategy."""
    
    async def test_truncate_with_no_middle_messages(self):
        """Test truncate with only system and recent messages."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "truncate",
            "preserve_recent": 4,
        })
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Msg1"},
            {"role": "assistant", "content": "Msg2"},
        ]
        
        result = await manager.manage_context(messages)
        
        # Should return unchanged (not enough to split)
        assert len(result) == 3
    
    async def test_truncate_drops_middle_messages(self):
        """Test truncate strategy drops middle messages."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "truncate",
            "preserve_recent": 2,
        })
        
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old message 1"},
            {"role": "assistant", "content": "Old response 1"},
            {"role": "user", "content": "Old message 2"},
            {"role": "assistant", "content": "Old response 2"},
            {"role": "user", "content": "Recent message"},
            {"role": "assistant", "content": "Recent response"},
        ]
        
        result = await manager.manage_context(messages)
        
        # Should have: 1 system + 2 recent = 3 messages
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["content"] == "Recent message"
        assert result[2]["content"] == "Recent response"
    
    async def test_truncate_preserves_all_system_messages(self):
        """Test that all system messages are preserved."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "truncate",
            "preserve_recent": 2,
        })
        
        messages = [
            {"role": "system", "content": "System 1"},
            {"role": "system", "content": "System 2"},
            {"role": "user", "content": "Old"},
            {"role": "user", "content": "Recent 1"},
            {"role": "user", "content": "Recent 2"},
        ]
        
        result = await manager.manage_context(messages)
        
        # Should have: 2 system + 2 recent = 4 messages
        assert len(result) == 4
        system_count = sum(1 for m in result if m["role"] == "system")
        assert system_count == 2


# ============================================================================
# Manage Context Tests - Summarize and Truncate Strategy
# ============================================================================

@pytest.mark.asyncio
class TestManageContextSummarize:
    """Tests for summarize_and_truncate strategy."""
    
    async def test_summarize_inserts_summary_message(self):
        """Test summarize strategy inserts summary before recent messages."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "summarize_and_truncate",
            "preserve_recent": 2,
        })
        
        async def mock_summarize(msgs):
            return f"Summary of {len(msgs)} messages"
        
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old message 1"},
            {"role": "assistant", "content": "Old response 1"},
            {"role": "user", "content": "Recent message"},
            {"role": "assistant", "content": "Recent response"},
        ]
        
        result = await manager.manage_context(messages, summarize_fn=mock_summarize)
        
        # Should have: 1 system + 1 summary + 2 recent = 4 messages
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert "[CONTEXT SUMMARY]" in result[1]["content"]
        assert "Summary of 2 messages" in result[1]["content"]
        assert result[1].get("msg_metadata", {}).get("type") == "context_summary"
    
    async def test_summarize_fallback_on_failure(self):
        """Test fallback to truncate when summarization fails."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "summarize_and_truncate",
            "preserve_recent": 2,
        })
        
        async def failing_summarize(msgs):
            raise Exception("Summarization failed")
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Old"},
            {"role": "user", "content": "Recent 1"},
            {"role": "user", "content": "Recent 2"},
        ]
        
        result = await manager.manage_context(messages, summarize_fn=failing_summarize)
        
        # Should fallback to truncate: 1 system + 2 recent = 3
        assert len(result) == 3
    
    async def test_summarize_fallback_no_function(self):
        """Test fallback to truncate when no summarize function provided."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "summarize_and_truncate",
            "preserve_recent": 2,
        })
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Old"},
            {"role": "user", "content": "Recent 1"},
            {"role": "user", "content": "Recent 2"},
        ]
        
        result = await manager.manage_context(messages, summarize_fn=None)
        
        # Should fallback to truncate
        assert len(result) == 3


# ============================================================================
# Edge Cases
# ============================================================================

@pytest.mark.asyncio
class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""
    
    async def test_empty_messages(self):
        """Test with empty message list."""
        manager = AgentLoopContextManager({"enabled": True})
        result = await manager.manage_context([])
        assert result == []
    
    async def test_only_system_message(self):
        """Test with only system message."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "preserve_recent": 4,
        })
        
        messages = [{"role": "system", "content": "System only"}]
        result = await manager.manage_context(messages)
        
        assert len(result) == 1
        assert result[0]["role"] == "system"
    
    async def test_fewer_messages_than_preserve_recent(self):
        """Test when total messages < preserve_recent."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "preserve_recent": 10,
        })
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User"},
            {"role": "assistant", "content": "Assistant"},
        ]
        
        result = await manager.manage_context(messages)
        
        # Should return all messages unchanged
        assert len(result) == 3
    
    async def test_exactly_at_preserve_recent(self):
        """Test when non-system messages == preserve_recent."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "preserve_recent": 2,
        })
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User 1"},
            {"role": "assistant", "content": "Assistant 1"},
        ]
        
        result = await manager.manage_context(messages)
        
        # Exactly 2 non-system messages = preserve_recent, no middle to drop
        assert len(result) == 3
    
    async def test_disabled_returns_unchanged(self):
        """Test disabled config returns messages unchanged."""
        manager = AgentLoopContextManager({"enabled": False})
        
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User"},
        ]
        
        result = await manager.manage_context(messages)
        
        assert result == messages


# ============================================================================
# Statistics Tests
# ============================================================================

@pytest.mark.asyncio
class TestStatistics:
    """Tests for statistics tracking."""
    
    async def test_stats_tracking(self):
        """Test that management stats are tracked correctly."""
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "truncate",
            "preserve_recent": 2,
        })
        
        messages = [
            {"role": "system", "content": "System prompt with some content"},
            {"role": "user", "content": "Old message to be dropped"},
            {"role": "user", "content": "Recent 1"},
            {"role": "user", "content": "Recent 2"},
        ]
        
        await manager.manage_context(messages)
        
        stats = manager.get_stats()
        assert stats["management_count"] == 1
        assert stats["tokens_saved"] > 0
    
    async def test_stats_reset(self):
        """Test stats reset functionality."""
        manager = AgentLoopContextManager({"enabled": True})
        manager._management_count = 5
        manager._tokens_saved = 1000
        
        manager.reset_stats()
        
        stats = manager.get_stats()
        assert stats["management_count"] == 0
        assert stats["tokens_saved"] == 0


# ============================================================================
# Integration with Message Objects
# ============================================================================

@pytest.mark.asyncio
class TestMessageObjects:
    """Tests with actual Message-like objects."""
    
    async def test_with_pydantic_like_messages(self):
        """Test with objects that have role/content attributes."""
        
        class MockMessage:
            def __init__(self, role, content):
                self.role = role
                self.content = content
        
        manager = AgentLoopContextManager({
            "enabled": True,
            "strategy": "truncate",
            "preserve_recent": 2,
        })
        
        messages = [
            MockMessage("system", "System"),
            MockMessage("user", "Old"),
            MockMessage("user", "Recent 1"),
            MockMessage("assistant", "Recent 2"),
        ]
        
        result = await manager.manage_context(messages)
        
        # Should have: 1 system + 2 recent = 3 messages
        assert len(result) == 3
