"""
Types for the summarizer engine and message lifecycle management.
"""

from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


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


class SummaryConfig(BaseModel):
    """
    User-facing configuration for summarization.

    Only two simple options:
    - enabled: Whether to enable summarization
    - retention_policy: What to do with summarized messages

    Internal settings like summary_ratio and model are handled automatically.
    """

    enabled: bool = Field(
        default=False,
        description="Enable summarization when history exceeds limits"
    )
    retention_policy: SummaryRetentionPolicy = Field(
        default=SummaryRetentionPolicy.KEEP,
        description="What to do with messages after summarization: 'keep' or 'delete'"
    )

    class Config:
        use_enum_values = True


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
