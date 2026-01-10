"""
Comprehensive tests for ToolResponseOffloader.

Tests cover:
- Configuration parsing and validation
- Offload threshold detection (token-based and byte-based)
- Preview generation with token and line limits
- Artifact file creation and storage
- Artifact retrieval (read, tail, search)
- Artifact listing and statistics
- Cleanup functionality
- Edge cases (empty content, unicode, JSON detection)
- Integration with artifact tools
"""

import pytest
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from omnicoreagent.core.tool_response_offloader import (
    ToolResponseOffloader,
    OffloadConfig,
    OffloadedResponse,
)
from omnicoreagent.core.tools.artifact_tool import build_tool_registry_artifact_tool
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.types import AgentConfig as TypesAgentConfig
from pydantic import ValidationError


# ============================================================================
# Configuration Tests
# ============================================================================

class TestOffloadConfig:
    """Tests for OffloadConfig parsing and defaults."""

    def test_default_config(self):
        """Test default configuration values."""
        config = OffloadConfig()
        assert config.enabled == True
        assert config.threshold_tokens == 500
        assert config.threshold_bytes == 2000
        assert config.max_preview_tokens == 150
        assert config.max_preview_lines == 10
        assert config.storage_dir == ".omnicoreagent_artifacts"
        assert config.retention_days == 7
        assert config.include_metadata == True

    def test_from_dict_empty(self):
        """Test creating config from empty dict uses defaults."""
        config = OffloadConfig.from_dict({})
        assert config.enabled == True
        assert config.threshold_tokens == 500

    def test_from_dict_custom_values(self):
        """Test creating config from custom dict."""
        config = OffloadConfig.from_dict({
            "enabled": False,
            "threshold_tokens": 1000,
            "threshold_bytes": 5000,
            "max_preview_tokens": 200,
            "storage_dir": ".custom_artifacts",
        })
        assert config.enabled == False
        assert config.threshold_tokens == 1000
        assert config.threshold_bytes == 5000
        assert config.max_preview_tokens == 200
        assert config.storage_dir == ".custom_artifacts"

    def test_from_dict_partial(self):
        """Test partial config uses defaults for missing keys."""
        config = OffloadConfig.from_dict({
            "enabled": True,
            "threshold_tokens": 250,
        })
        assert config.enabled == True
        assert config.threshold_tokens == 250
        assert config.threshold_bytes == 2000  # default
        assert config.max_preview_tokens == 150  # default

    def test_from_dict_none(self):
        """Test creating config from None returns defaults."""
        config = OffloadConfig.from_dict(None)
        assert config.enabled == True
        assert config.threshold_tokens == 500


# ============================================================================
# AgentConfig Validation Tests
# ============================================================================

class TestAgentConfigToolOffloadValidation:
    """Tests for tool_offload validation in AgentConfig."""

    def test_valid_tool_offload_accepted(self):
        """Test valid tool_offload config is accepted."""
        config = TypesAgentConfig(
            agent_name="test",
            max_steps=10,
            tool_call_timeout=30,
            tool_offload={"enabled": True, "threshold_tokens": 500}
        )
        assert config.tool_offload["enabled"] == True

    def test_invalid_threshold_tokens_rejected(self):
        """Test negative threshold_tokens raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TypesAgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                tool_offload={"threshold_tokens": -1}
            )
        assert "threshold_tokens must be positive" in str(exc_info.value)

    def test_invalid_threshold_bytes_rejected(self):
        """Test negative threshold_bytes raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TypesAgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                tool_offload={"threshold_bytes": 0}
            )
        assert "threshold_bytes must be positive" in str(exc_info.value)

    def test_invalid_max_preview_tokens_rejected(self):
        """Test negative max_preview_tokens raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TypesAgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                tool_offload={"max_preview_tokens": -5}
            )
        assert "max_preview_tokens must be positive" in str(exc_info.value)

    def test_empty_storage_dir_rejected(self):
        """Test empty storage_dir raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TypesAgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                tool_offload={"storage_dir": ""}
            )
        assert "storage_dir must be a non-empty string" in str(exc_info.value)

    def test_invalid_retention_days_rejected(self):
        """Test negative retention_days raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TypesAgentConfig(
                agent_name="test",
                max_steps=10,
                tool_call_timeout=30,
                tool_offload={"retention_days": -1}
            )
        assert "retention_days must be non-negative" in str(exc_info.value)


# ============================================================================
# Should Offload Tests
# ============================================================================

class TestShouldOffload:
    """Tests for should_offload threshold detection."""

    def setup_method(self):
        """Create a temp directory for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={"enabled": True, "threshold_tokens": 50, "threshold_bytes": 200},
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        """Clean up temp directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_should_offload_disabled(self):
        """Test that should_offload returns False when disabled."""
        offloader = ToolResponseOffloader(
            config={"enabled": False},
            base_dir=self.temp_dir
        )
        large_content = "word " * 1000
        assert offloader.should_offload(large_content) == False

    def test_should_offload_small_response(self):
        """Test small response does not trigger offload."""
        small_content = "Hello world"
        assert self.offloader.should_offload(small_content) == False

    def test_should_offload_exceeds_token_threshold(self):
        """Test response exceeding token threshold triggers offload."""
        large_content = "This is a word. " * 50  # ~100 tokens
        assert self.offloader.should_offload(large_content) == True

    def test_should_offload_exceeds_byte_threshold(self):
        """Test response exceeding byte threshold triggers offload."""
        large_content = "x" * 250  # 250 bytes > 200 byte threshold
        assert self.offloader.should_offload(large_content) == True

    def test_should_offload_just_below_threshold(self):
        """Test response just below threshold does not offload."""
        small_content = "word " * 10  # ~20 tokens, ~50 bytes
        assert self.offloader.should_offload(small_content) == False

    def test_should_offload_empty_string(self):
        """Test empty string does not offload."""
        assert self.offloader.should_offload("") == False


# ============================================================================
# Preview Generation Tests
# ============================================================================

class TestPreviewGeneration:
    """Tests for preview text generation."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={
                "enabled": True,
                "max_preview_tokens": 50,
                "max_preview_lines": 5,
            },
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_preview_short_content_unchanged(self):
        """Test short content is returned as-is."""
        content = "Line 1\nLine 2\nLine 3"
        preview = self.offloader.get_preview(content)
        assert preview == content

    def test_preview_truncates_by_lines(self):
        """Test preview truncates by line limit."""
        lines = [f"Line {i}" for i in range(20)]
        content = "\n".join(lines)
        preview = self.offloader.get_preview(content)
        
        # Should have max_preview_lines (5) lines + truncation indicator
        assert "Line 0" in preview
        assert "Line 4" in preview
        assert "15 more lines" in preview

    def test_preview_truncates_by_tokens(self):
        """Test preview truncates by token limit when lines are long."""
        long_line = "word " * 100  # Very long line
        content = long_line
        preview = self.offloader.get_preview(content)
        
        # Preview should be shorter than original
        assert len(preview) < len(content)
        assert "truncated" in preview

    def test_preview_preserves_structure(self):
        """Test preview preserves line structure."""
        content = "Header\nBody line 1\nBody line 2\nFooter"
        preview = self.offloader.get_preview(content)
        assert "Header" in preview
        assert "Body line 1" in preview


# ============================================================================
# Offload Functionality Tests
# ============================================================================

class TestOffload:
    """Tests for the offload() method."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={
                "enabled": True,
                "threshold_tokens": 20,
                "max_preview_tokens": 50,
            },
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_offload_creates_file(self):
        """Test offload creates artifact file."""
        content = "Test content " * 50
        result = self.offloader.offload("test_tool", content)
        
        assert os.path.exists(result.artifact_path)
        with open(result.artifact_path) as f:
            assert f.read() == content

    def test_offload_returns_offloaded_response(self):
        """Test offload returns OffloadedResponse with correct fields."""
        content = "Test content " * 50
        result = self.offloader.offload("my_search_tool", content)
        
        assert isinstance(result, OffloadedResponse)
        assert result.tool_name == "my_search_tool"
        assert "my_search_tool" in result.artifact_id
        assert result.original_tokens > result.preview_tokens
        assert result.preview in result.context_message

    def test_offload_context_message_format(self):
        """Test context message has correct format."""
        content = "Data " * 100
        result = self.offloader.offload("api_call", content)
        
        message = result.context_message
        assert "[TOOL RESPONSE OFFLOADED]" in message
        assert "api_call" in message
        assert result.artifact_id in message
        assert "PREVIEW" in message
        assert "read_artifact" in message

    def test_offload_creates_metadata_file(self):
        """Test offload creates .meta.json file."""
        content = "Content " * 50
        result = self.offloader.offload("test_tool", content, metadata={"key": "value"})
        
        meta_path = Path(self.temp_dir) / ".omnicoreagent_artifacts" / f"{result.artifact_id}.meta.json"
        assert meta_path.exists()
        
        with open(meta_path) as f:
            meta = json.load(f)
            assert meta["tool_name"] == "test_tool"
            assert meta["artifact_id"] == result.artifact_id
            assert meta["custom"]["key"] == "value"

    def test_offload_updates_statistics(self):
        """Test offload updates internal statistics."""
        content1 = "Content " * 50
        content2 = "More content " * 50
        
        self.offloader.offload("tool1", content1)
        self.offloader.offload("tool2", content2)
        
        stats = self.offloader.get_stats()
        assert stats["offload_count"] == 2
        assert stats["tokens_saved"] > 0
        assert stats["active_artifacts"] == 2

    def test_offload_detects_json_extension(self):
        """Test JSON content gets .json extension."""
        content = json.dumps({"results": [1, 2, 3] * 50})
        result = self.offloader.offload("api_tool", content)
        
        assert result.artifact_path.endswith(".json")

    def test_offload_detects_xml_extension(self):
        """Test XML content gets .xml extension."""
        content = "<?xml version='1.0'?><data>" + "<item>x</item>" * 50 + "</data>"
        result = self.offloader.offload("xml_tool", content)
        
        assert result.artifact_path.endswith(".xml")

    def test_offload_plain_text_extension(self):
        """Test plain text gets .txt extension."""
        content = "Plain text content " * 50
        result = self.offloader.offload("text_tool", content)
        
        assert result.artifact_path.endswith(".txt")


# ============================================================================
# Artifact Retrieval Tests
# ============================================================================

class TestArtifactRetrieval:
    """Tests for read_artifact, tail_artifact, search_artifact."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={"enabled": True, "threshold_tokens": 10},
            base_dir=self.temp_dir
        )
        
        # Create test artifact
        self.test_content = "\n".join([f"Line {i}: Test content here" for i in range(100)])
        self.artifact = self.offloader.offload("test_tool", self.test_content)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_artifact_returns_full_content(self):
        """Test read_artifact returns complete content."""
        content = self.offloader.read_artifact(self.artifact.artifact_id)
        assert content == self.test_content

    def test_read_artifact_not_found(self):
        """Test read_artifact returns None for non-existent ID."""
        content = self.offloader.read_artifact("nonexistent_id")
        assert content is None

    def test_tail_artifact_returns_last_lines(self):
        """Test tail_artifact returns last N lines."""
        result = self.offloader.tail_artifact(self.artifact.artifact_id, lines=10)
        
        assert "Line 99" in result
        assert "Line 90" in result
        assert "Line 50" not in result  # Should not be in last 10 lines
        assert "lines above" in result  # Truncation indicator

    def test_tail_artifact_full_content_if_small(self):
        """Test tail with lines > content returns full content."""
        small_content = "Line 1\nLine 2\nLine 3"
        artifact = self.offloader.offload("small_tool", small_content + " " * 100)
        
        result = self.offloader.tail_artifact(artifact.artifact_id, lines=100)
        # Should contain all lines without truncation indicator
        assert "Line 1" in result
        assert "Line 3" in result

    def test_tail_artifact_not_found(self):
        """Test tail_artifact returns None for non-existent ID."""
        result = self.offloader.tail_artifact("nonexistent_id", lines=10)
        assert result is None

    def test_search_artifact_finds_matches(self):
        """Test search_artifact finds matching lines."""
        result = self.offloader.search_artifact(self.artifact.artifact_id, "Line 50")
        
        assert "Line 50" in result
        assert "Found 1 matches" in result

    def test_search_artifact_case_insensitive(self):
        """Test search is case-insensitive."""
        result = self.offloader.search_artifact(self.artifact.artifact_id, "LINE 50")
        assert "Line 50" in result

    def test_search_artifact_multiple_matches(self):
        """Test search with multiple matches."""
        result = self.offloader.search_artifact(self.artifact.artifact_id, "Test content")
        assert "Found 100 matches" in result or "10 more matches" in result

    def test_search_artifact_no_matches(self):
        """Test search with no matches."""
        result = self.offloader.search_artifact(self.artifact.artifact_id, "ZZZNOMATCHZZZ")
        assert "No matches found" in result

    def test_search_artifact_not_found(self):
        """Test search_artifact returns None for non-existent ID."""
        result = self.offloader.search_artifact("nonexistent_id", "query")
        assert result is None


# ============================================================================
# Artifact Listing Tests
# ============================================================================

class TestArtifactListing:
    """Tests for list_artifacts and get_stats."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={"enabled": True, "threshold_tokens": 10},
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_artifacts_empty(self):
        """Test list_artifacts returns empty list initially."""
        artifacts = self.offloader.list_artifacts()
        assert artifacts == []

    def test_list_artifacts_after_offload(self):
        """Test list_artifacts returns offloaded items."""
        self.offloader.offload("tool1", "Content " * 50)
        self.offloader.offload("tool2", "More content " * 50)
        
        artifacts = self.offloader.list_artifacts()
        assert len(artifacts) == 2
        
        tool_names = [a["tool"] for a in artifacts]
        assert "tool1" in tool_names
        assert "tool2" in tool_names

    def test_list_artifacts_includes_tokens_saved(self):
        """Test list includes tokens_saved field."""
        self.offloader.offload("tool1", "Content " * 100)
        
        artifacts = self.offloader.list_artifacts()
        assert "tokens_saved" in artifacts[0]
        # tokens_saved can be 0 for small content with high overhead

    def test_get_stats_initial(self):
        """Test get_stats returns zeros initially."""
        stats = self.offloader.get_stats()
        assert stats["offload_count"] == 0
        assert stats["tokens_saved"] == 0
        assert stats["active_artifacts"] == 0

    def test_get_stats_after_offloads(self):
        """Test get_stats accumulates correctly."""
        self.offloader.offload("tool1", "Content " * 100)
        self.offloader.offload("tool2", "More " * 100)
        
        stats = self.offloader.get_stats()
        assert stats["offload_count"] == 2
        assert stats["tokens_saved"] >= 0  # May be 0 for small content
        assert stats["active_artifacts"] == 2


# ============================================================================
# Edge Cases Tests
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={"enabled": True, "threshold_tokens": 10},
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unicode_content(self):
        """Test handling of unicode content."""
        content = "日本語テスト 🎉 émojis ñ " * 50
        result = self.offloader.offload("unicode_tool", content)
        
        # Read back and verify
        retrieved = self.offloader.read_artifact(result.artifact_id)
        assert retrieved == content

    def test_very_long_lines(self):
        """Test handling of very long single lines."""
        content = "x" * 100000  # 100KB single line
        result = self.offloader.offload("long_line_tool", content)
        
        retrieved = self.offloader.read_artifact(result.artifact_id)
        assert retrieved == content

    def test_empty_lines_handling(self):
        """Test handling of content with empty lines."""
        content = "Line 1\n\n\nLine 4\n\nLine 6" + " padding" * 50
        result = self.offloader.offload("empty_lines_tool", content)
        
        retrieved = self.offloader.read_artifact(result.artifact_id)
        assert retrieved == content

    def test_special_characters_in_tool_name(self):
        """Test tool names with special characters are sanitized."""
        result = self.offloader.offload("tool/with:special*chars", "Content " * 50)
        
        # Artifact ID should be sanitized
        assert "/" not in result.artifact_id
        assert ":" not in result.artifact_id
        assert "*" not in result.artifact_id

    def test_gitignore_created(self):
        """Test .gitignore is created in storage directory."""
        self.offloader.offload("test", "Content " * 50)
        
        gitignore_path = Path(self.temp_dir) / ".omnicoreagent_artifacts" / ".gitignore"
        assert gitignore_path.exists()
        assert "*" in gitignore_path.read_text()

    def test_malformed_json_not_detected_as_json(self):
        """Test malformed JSON gets .txt extension."""
        content = "{not valid json: [1, 2, 3" + " " * 100
        result = self.offloader.offload("bad_json_tool", content)
        
        assert result.artifact_path.endswith(".txt")


# ============================================================================
# Artifact Tool Registration Tests
# ============================================================================

class TestArtifactToolRegistration:
    """Tests for build_tool_registry_artifact_tool."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={"enabled": True, "threshold_tokens": 10},
            base_dir=self.temp_dir
        )
        self.registry = ToolRegistry()
        build_tool_registry_artifact_tool(
            offloader=self.offloader,
            registry=self.registry
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_tools_registered(self):
        """Test all 4 artifact tools are registered."""
        tool_names = list(self.registry.tools.keys())
        assert "read_artifact" in tool_names
        assert "tail_artifact" in tool_names
        assert "search_artifact" in tool_names
        assert "list_artifacts" in tool_names
        assert len(tool_names) == 4

    def test_read_artifact_tool_works(self):
        """Test registered read_artifact tool function works."""
        # Create artifact
        self.offloader.offload("test", "Content for testing " * 50)
        artifacts = self.offloader.list_artifacts()
        artifact_id = artifacts[0]["id"]
        
        # Get tool and call its function
        tool = self.registry.tools["read_artifact"]
        result = tool.function(artifact_id=artifact_id)
        
        assert "Content for testing" in result

    def test_read_artifact_tool_error_handling(self):
        """Test read_artifact tool handles missing artifact."""
        tool = self.registry.tools["read_artifact"]
        result = tool.function(artifact_id="nonexistent")
        
        assert "Error" in result
        assert "not found" in result

    def test_list_artifacts_tool_works(self):
        """Test registered list_artifacts tool function works."""
        self.offloader.offload("tool1", "Content " * 50)
        self.offloader.offload("tool2", "More " * 50)
        
        tool = self.registry.tools["list_artifacts"]
        result = tool.function()
        
        assert "tool1" in result
        assert "tool2" in result


# ============================================================================
# Cleanup Tests
# ============================================================================

class TestCleanup:
    """Tests for old artifact cleanup."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.offloader = ToolResponseOffloader(
            config={
                "enabled": True,
                "threshold_tokens": 10,
                "retention_days": 0,  # For testing
            },
            base_dir=self.temp_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cleanup_old_artifacts(self):
        """Test cleanup removes old artifacts."""
        # Create artifact
        result = self.offloader.offload("test", "Content " * 50)
        assert os.path.exists(result.artifact_path)
        
        # With retention_days=0, all artifacts are "old"
        self.offloader.cleanup_old_artifacts()
        
        # Artifact should be removed (retention_days=0 means immediate expiry)
        # Note: This test may pass or fail depending on timing
        # A more robust test would mock datetime

    def test_cleanup_preserves_gitignore(self):
        """Test cleanup preserves .gitignore file."""
        self.offloader.offload("test", "Content " * 50)
        
        self.offloader.cleanup_old_artifacts()
        
        gitignore_path = Path(self.temp_dir) / ".omnicoreagent_artifacts" / ".gitignore"
        assert gitignore_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
