"""
Types for the summarizer engine and message lifecycle management.
"""

from dataclasses import dataclass
from enum import Enum


class MessageStatus(str, Enum):
    """Status of a message in the message store."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class InactiveReason(str, Enum):
    """Reason why a message is inactive."""

    SUMMARIZED = "summarized"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SummaryRetentionPolicy(str, Enum):
    """Policy for handling summarized messages."""

    KEEP = "keep"
    DELETE = "delete"


@dataclass
class SummaryConfig:
    """
    User-facing configuration for summarization.

    Only two simple options:
    - enabled: Whether to enable summarization
    - retention_policy: What to do with summarized messages

    Internal settings like summary_ratio and model are handled automatically.
    """

    enabled: bool = False
    retention_policy: SummaryRetentionPolicy | str = SummaryRetentionPolicy.KEEP

    def __post_init__(self):
        self.retention_policy = SummaryRetentionPolicy(self.retention_policy)


SUMMARY_TAG = "[CONVERSATION SUMMARY]"


def format_summary_content(summary_text: str) -> str:
    """
    Format summary text with the conversation summary tag.

    Args:
        summary_text: The raw summary text from the LLM

    Returns:
        str: Formatted content with tag prefix
    """
    return f"{SUMMARY_TAG}\n{summary_text}"


def is_summary_message(msg: dict) -> bool:
    """
    Check if a message is a summary message.

    Args:
        msg: Message dictionary

    Returns:
        bool: True if this is a summary message
    """
    metadata = msg.get("msg_metadata", {})
    return metadata.get("type") == "history_summary"
