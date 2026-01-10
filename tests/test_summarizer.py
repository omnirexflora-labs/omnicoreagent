"""
Tests for the summarizer engine and tokenizer.
"""

import pytest
from omnicoreagent.core.summarizer.tokenizer import (
    count_tokens,
    count_message_tokens,
    get_encoding,
    DEFAULT_SUMMARY_RATIO,
)
from omnicoreagent.core.summarizer.summarizer_types import (
    SummaryConfig,
    MessageStatus,
    InactiveReason,
    SummaryRetentionPolicy,
    format_summary_content,
    is_summary_message,
    SUMMARY_TAG,
)
from omnicoreagent.core.summarizer.summarizer_engine import (
    prepare_history_sliding_window,
    prepare_history_token_budget,
)


class TestTokenizer:
    """Tests for tiktoken-based token counting."""

    def test_count_tokens_basic(self):
        """Test basic token counting."""
        text = "Hello, world!"
        tokens = count_tokens(text)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_count_tokens_empty(self):
        """Test empty string returns 0 tokens."""
        assert count_tokens("") == 0
        assert count_tokens(None) == 0  # Should handle None gracefully

    def test_count_tokens_long_text(self):
        """Test longer text has more tokens."""
        short = "Hi"
        long = "Hello, this is a much longer sentence with many more words."
        assert count_tokens(long) > count_tokens(short)

    def test_count_message_tokens(self):
        """Test counting tokens across multiple messages."""
        messages = [
            {"content": "Hello"},
            {"content": "How are you?"},
            {"content": "I'm fine, thanks!"},
        ]
        total = count_message_tokens(messages)
        assert total > 0
        # Should be sum of individual token counts
        individual_sum = sum(count_tokens(m["content"]) for m in messages)
        assert total == individual_sum

    def test_count_message_tokens_empty_content(self):
        """Test messages with empty or missing content."""
        messages = [
            {"content": "Hello"},
            {"content": ""},
            {},  # No content field
        ]
        total = count_message_tokens(messages)
        assert total == count_tokens("Hello")

    def test_get_encoding_caching(self):
        """Test that encodings are cached."""
        enc1 = get_encoding("gpt-4")
        enc2 = get_encoding("gpt-4")
        assert enc1 is enc2  # Same object due to caching


class TestSummaryConfig:
    """Tests for SummaryConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SummaryConfig()
        assert config.enabled is False
        assert config.retention_policy == SummaryRetentionPolicy.KEEP

    def test_enabled_config(self):
        """Test enabled configuration."""
        config = SummaryConfig(enabled=True)
        assert config.enabled is True

    def test_retention_policy_delete(self):
        """Test delete retention policy."""
        config = SummaryConfig(
            enabled=True,
            retention_policy=SummaryRetentionPolicy.DELETE
        )
        assert config.retention_policy == SummaryRetentionPolicy.DELETE


class TestSummaryTypes:
    """Tests for summary helper functions."""

    def test_format_summary_content(self):
        """Test summary content formatting."""
        summary = "This is a summary of the conversation."
        formatted = format_summary_content(summary)
        assert formatted.startswith(SUMMARY_TAG)
        assert summary in formatted

    def test_is_summary_message_true(self):
        """Test identifying a summary message."""
        msg = {
            "content": "Summary text",
            "msg_metadata": {"type": "history_summary", "summarizes": ["id1", "id2"]}
        }
        assert is_summary_message(msg) is True

    def test_is_summary_message_false(self):
        """Test regular message is not identified as summary."""
        msg = {"content": "Regular message", "msg_metadata": {"agent_name": "test"}}
        assert is_summary_message(msg) is False

    def test_is_summary_message_no_metadata(self):
        """Test message without metadata."""
        msg = {"content": "No metadata"}
        assert is_summary_message(msg) is False


@pytest.mark.asyncio
class TestSlidingWindowSummarizer:
    """Tests for sliding window summarization."""

    async def test_no_overflow_returns_as_is(self):
        """Test that messages within window size are returned unchanged."""
        messages = [
            {"id": "1", "content": "Hello"},
            {"id": "2", "content": "Hi there"},
        ]
        config = SummaryConfig(enabled=True)

        result, summarized_ids = await prepare_history_sliding_window(
            messages, window_size=5, summarize_fn=None, summary_config=config
        )

        assert result == messages
        assert summarized_ids == []

    async def test_overflow_without_summarization_truncates(self):
        """Test truncation when summarization is disabled."""
        messages = [
            {"id": "1", "content": "Message 1"},
            {"id": "2", "content": "Message 2"},
            {"id": "3", "content": "Message 3"},
            {"id": "4", "content": "Message 4"},
            {"id": "5", "content": "Message 5"},
        ]
        config = SummaryConfig(enabled=False)

        result, summarized_ids = await prepare_history_sliding_window(
            messages, window_size=3, summarize_fn=None, summary_config=config
        )

        # Should return last 3 messages
        assert len(result) == 3
        assert result[0]["id"] == "3"
        assert result[1]["id"] == "4"
        assert result[2]["id"] == "5"
        assert summarized_ids == []

    async def test_overflow_with_summarization(self):
        """Test summarization when overflow and enabled."""
        messages = [
            {"id": "1", "content": "Message 1"},
            {"id": "2", "content": "Message 2"},
            {"id": "3", "content": "Message 3"},
            {"id": "4", "content": "Message 4"},
            {"id": "5", "content": "Message 5"},
        ]
        config = SummaryConfig(enabled=True)

        async def mock_summarize(msgs):
            return f"Summary of {len(msgs)} messages"

        result, summarized_ids = await prepare_history_sliding_window(
            messages, window_size=3, summarize_fn=mock_summarize, summary_config=config
        )

        # Should be window_size messages: 1 summary + 2 recent
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert SUMMARY_TAG in result[0]["content"]
        assert result[1]["id"] == "4"
        assert result[2]["id"] == "5"
        # Messages 1, 2, 3 should be summarized
        assert summarized_ids == ["1", "2", "3"]


@pytest.mark.asyncio
class TestTokenBudgetSummarizer:
    """Tests for token budget summarization."""

    async def test_within_budget_returns_as_is(self):
        """Test that messages within token budget are returned unchanged."""
        messages = [
            {"id": "1", "content": "Hello"},
            {"id": "2", "content": "World"},
        ]
        config = SummaryConfig(enabled=True)

        result, summarized_ids = await prepare_history_token_budget(
            messages, max_tokens=1000, summarize_fn=None, summary_config=config
        )

        assert result == messages
        assert summarized_ids == []

    async def test_overflow_without_summarization_truncates(self):
        """Test truncation when summarization is disabled but over budget."""
        # Create messages that exceed budget
        messages = [
            {"id": "1", "content": "This is message one with some content."},
            {"id": "2", "content": "This is message two with more content."},
            {"id": "3", "content": "This is message three with even more."},
        ]
        config = SummaryConfig(enabled=False)

        # Small budget that won't fit all messages
        result, summarized_ids = await prepare_history_token_budget(
            messages, max_tokens=20, summarize_fn=None, summary_config=config
        )

        # Should truncate from front
        assert len(result) < len(messages)
        assert summarized_ids == []

    async def test_overflow_with_summarization(self):
        """Test summarization when over budget and enabled."""
        # Create messages that will definitely exceed budget
        messages = [
            {"id": "1", "content": "This is the first message with lots of text that takes up many tokens."},
            {"id": "2", "content": "This is the second message with even more text that takes up tokens."},
            {"id": "3", "content": "This is the third message with additional content."},
            {"id": "4", "content": "Fourth message."},
            {"id": "5", "content": "Fifth."},
        ]
        config = SummaryConfig(enabled=True)

        async def mock_summarize(msgs, max_tokens):
            return f"Summary of {len(msgs)} messages"

        # Very small budget to force summarization
        result, summarized_ids = await prepare_history_token_budget(
            messages, max_tokens=15, summarize_fn=mock_summarize, summary_config=config
        )

        # Should have at least one message (summary or recent)
        assert len(result) > 0
        # If summarization happened, first message should be summary
        if summarized_ids:
            assert result[0]["role"] == "user"
            assert SUMMARY_TAG in result[0]["content"]


class TestMessageStatus:
    """Tests for message lifecycle enums."""

    def test_message_status_values(self):
        """Test MessageStatus enum values."""
        assert MessageStatus.ACTIVE == "active"
        assert MessageStatus.INACTIVE == "inactive"

    def test_inactive_reason_values(self):
        """Test InactiveReason enum values."""
        assert InactiveReason.SUMMARIZED == "summarized"
        assert InactiveReason.ARCHIVED == "archived"
        assert InactiveReason.DELETED == "deleted"
