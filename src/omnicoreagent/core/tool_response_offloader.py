"""
Tool Response Offloader

Offloads large tool responses to the file system to reduce context bloat.
Only a preview is loaded into the LLM context, with a reference to the full
content that the agent can access on demand.

Inspired by:
- Cursor's "Turning long tool responses into files" pattern
- Anthropic's "Context efficient tool results" pattern
"""

import json
import os
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta

from omnicoreagent.core.summarizer.tokenizer import count_tokens
from omnicoreagent.core.utils import logger


@dataclass
class OffloadConfig:
    """Configuration for tool response offloading."""
    
    enabled: bool = True
    threshold_tokens: int = 500
    threshold_bytes: int = 2000
    max_preview_tokens: int = 150
    max_preview_lines: int = 10
    storage_dir: str = ".omnicoreagent_artifacts"
    retention_days: int = 7
    include_metadata: bool = True
    
    @classmethod
    def from_dict(cls, config: dict) -> "OffloadConfig":
        """Create config from dictionary."""
        if not config:
            return cls()
        
        return cls(
            enabled=config.get("enabled", True),
            threshold_tokens=config.get("threshold_tokens", 500),
            threshold_bytes=config.get("threshold_bytes", 2000),
            max_preview_tokens=config.get("max_preview_tokens", 150),
            max_preview_lines=config.get("max_preview_lines", 10),
            storage_dir=config.get("storage_dir", ".omnicoreagent_artifacts"),
            retention_days=config.get("retention_days", 7),
            include_metadata=config.get("include_metadata", True),
        )


@dataclass
class OffloadedResponse:
    """Represents an offloaded tool response."""
    
    artifact_id: str
    artifact_path: str
    preview: str
    original_tokens: int
    preview_tokens: int
    tool_name: str
    timestamp: str
    
    @property
    def context_message(self) -> str:
        """Generate the message to include in LLM context."""
        tokens_saved = self.original_tokens - self.preview_tokens
        return (
            f"[TOOL RESPONSE OFFLOADED]\n"
            f"Tool: {self.tool_name}\n"
            f"Artifact ID: {self.artifact_id}\n"
            f"Original size: {self.original_tokens} tokens → Preview: {self.preview_tokens} tokens "
            f"(saved {tokens_saved} tokens)\n\n"
            f"--- PREVIEW ---\n{self.preview}\n"
            f"--- END PREVIEW ---\n\n"
            f"📁 Full response saved to: {self.artifact_path}\n"
            f"💡 Use read_artifact('{self.artifact_id}') to load full content when needed."
        )


class ToolResponseOffloader:
    """
    Manages offloading of large tool responses to the file system.
    
    When a tool returns a response exceeding the configured threshold,
    this class:
    1. Saves the full response to a file
    2. Creates a preview (first N tokens/lines)
    3. Returns a context-efficient message with preview + file reference
    
    The agent is given tools to read the full content on demand.
    """
    
    def __init__(self, config: OffloadConfig | dict = None, base_dir: str = None):
        if isinstance(config, dict):
            config = OffloadConfig.from_dict(config)
        self.config = config or OffloadConfig()
        
        self.base_dir = base_dir or os.getcwd()
        self.storage_path = Path(self.base_dir) / self.config.storage_dir
        
        if self.config.enabled:
            self._ensure_storage_dir()
        
        self._artifacts: Dict[str, OffloadedResponse] = {}
        
        self._offload_count = 0
        self._tokens_saved = 0
    
    def _ensure_storage_dir(self):
        """Create storage directory if it doesn't exist."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        gitignore_path = self.storage_path / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text("*\n!.gitignore\n")
    
    def _generate_artifact_id(self, tool_name: str, content: str) -> str:
        """Generate a unique but readable artifact ID."""
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        clean_name = "".join(c if c.isalnum() or c == "_" else "_" for c in tool_name)
        
        return f"{clean_name}_{timestamp}_{content_hash}"
    
    def should_offload(self, response: str) -> bool:
        """
        Check if a tool response should be offloaded.
        
        Args:
            response: The tool response string
            
        Returns:
            True if the response exceeds configured thresholds
        """
        if not self.config.enabled:
            return False
        
        if len(response.encode('utf-8')) > self.config.threshold_bytes:
            return True
        
        token_count = count_tokens(response)
        if token_count > self.config.threshold_tokens:
            return True
        
        return False
    
    def get_preview(self, response: str) -> str:
        """
        Generate a preview of the response.
        
        Uses both line limit and token limit, whichever is more constraining.
        
        Args:
            response: Full response text
            
        Returns:
            Truncated preview text
        """
        lines = response.split('\n')
        
        preview_lines = lines[:self.config.max_preview_lines]
        preview = '\n'.join(preview_lines)
        
        preview_tokens = count_tokens(preview)
        
        if preview_tokens <= self.config.max_preview_tokens:
            if len(lines) > self.config.max_preview_lines:
                preview += f"\n... [{len(lines) - self.config.max_preview_lines} more lines]"
            return preview
        
        target_chars = int(len(preview) * (self.config.max_preview_tokens / preview_tokens))
        preview = preview[:target_chars]
        
        remaining_lines = len(lines) - preview.count('\n')
        preview += f"\n... [truncated, {remaining_lines} more lines]"
        
        return preview
    
    def offload(
        self,
        tool_name: str,
        response: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OffloadedResponse:
        """
        Offload a tool response to the file system.
        
        Args:
            tool_name: Name of the tool that produced the response
            response: Full response text
            metadata: Optional metadata to store alongside
            
        Returns:
            OffloadedResponse with preview and file reference
        """
        artifact_id = self._generate_artifact_id(tool_name, response)
        
        extension = self._detect_extension(response)
        filename = f"{artifact_id}{extension}"
        artifact_path = self.storage_path / filename
        
        original_tokens = count_tokens(response)
        preview = self.get_preview(response)
        preview_tokens = count_tokens(preview)
        
        artifact_path.write_text(response, encoding='utf-8')
        
        timestamp = datetime.now().isoformat()
        if self.config.include_metadata:
            meta = {
                "artifact_id": artifact_id,
                "tool_name": tool_name,
                "original_tokens": original_tokens,
                "original_bytes": len(response.encode('utf-8')),
                "timestamp": timestamp,
                "custom": metadata or {},
            }
            meta_path = self.storage_path / f"{artifact_id}.meta.json"
            meta_path.write_text(json.dumps(meta, indent=2))
        
        offloaded = OffloadedResponse(
            artifact_id=artifact_id,
            artifact_path=str(artifact_path),
            preview=preview,
            original_tokens=original_tokens,
            preview_tokens=preview_tokens,
            tool_name=tool_name,
            timestamp=timestamp,
        )
        
        self._artifacts[artifact_id] = offloaded
        self._offload_count += 1
        self._tokens_saved += (original_tokens - preview_tokens)
        
        logger.info(
            f"Offloaded tool response: {tool_name} -> {artifact_id} "
            f"({original_tokens} → {preview_tokens} tokens, saved {original_tokens - preview_tokens})"
        )
        
        return offloaded
    
    def _detect_extension(self, content: str) -> str:
        """Detect appropriate file extension for content."""
        content_stripped = content.strip()
        
        if content_stripped.startswith('{') or content_stripped.startswith('['):
            try:
                json.loads(content_stripped)
                return ".json"
            except json.JSONDecodeError:
                pass
        
        if content_stripped.startswith('<?xml') or content_stripped.startswith('<'):
            return ".xml"
        
        return ".txt"
    
    def read_artifact(self, artifact_id: str) -> Optional[str]:
        """
        Read full content of an offloaded artifact.
        
        Args:
            artifact_id: The artifact ID to read
            
        Returns:
            Full content string, or None if not found
        """
        if artifact_id in self._artifacts:
            artifact = self._artifacts[artifact_id]
            artifact_path = Path(artifact.artifact_path)
            if artifact_path.exists():
                return artifact_path.read_text(encoding='utf-8')
        
        for ext in [".txt", ".json", ".xml"]:
            path = self.storage_path / f"{artifact_id}{ext}"
            if path.exists():
                return path.read_text(encoding='utf-8')
        
        return None
    
    def tail_artifact(self, artifact_id: str, lines: int = 50) -> Optional[str]:
        """
        Read last N lines of an offloaded artifact.
        
        Args:
            artifact_id: The artifact ID to read
            lines: Number of lines from end
            
        Returns:
            Last N lines, or None if not found
        """
        content = self.read_artifact(artifact_id)
        if content is None:
            return None
        
        all_lines = content.split('\n')
        tail_lines = all_lines[-lines:]
        
        if len(all_lines) > lines:
            return f"... [{len(all_lines) - lines} lines above]\n" + '\n'.join(tail_lines)
        
        return '\n'.join(tail_lines)
    
    def search_artifact(self, artifact_id: str, query: str) -> Optional[str]:
        """
        Search within an offloaded artifact.
        
        Args:
            artifact_id: The artifact ID to search
            query: Search query (case-insensitive)
            
        Returns:
            Matching lines with context, or None if not found
        """
        content = self.read_artifact(artifact_id)
        if content is None:
            return None
        
        lines = content.split('\n')
        matches = []
        
        for i, line in enumerate(lines):
            if query.lower() in line.lower():
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                context_lines = lines[start:end]
                matches.append(f"Line {i+1}:\n" + '\n'.join(context_lines))
        
        if not matches:
            return f"No matches found for '{query}'"
        
        result = f"Found {len(matches)} matches for '{query}':\n\n"
        result += "\n---\n".join(matches[:10])
        
        if len(matches) > 10:
            result += f"\n\n... and {len(matches) - 10} more matches"
        
        return result
    
    def cleanup_old_artifacts(self):
        """Remove artifacts older than retention period."""
        if not self.storage_path.exists():
            return
        
        cutoff = datetime.now() - timedelta(days=self.config.retention_days)
        removed_count = 0
        
        for path in self.storage_path.iterdir():
            if path.name == ".gitignore":
                continue
            
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
                if mtime < cutoff:
                    path.unlink()
                    removed_count += 1
            except Exception as e:
                logger.warning(f"Failed to cleanup artifact {path}: {e}")
        
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old artifacts")
    
    def get_stats(self) -> dict:
        """Get offloading statistics."""
        return {
            "offload_count": self._offload_count,
            "tokens_saved": self._tokens_saved,
            "active_artifacts": len(self._artifacts),
            "config": {
                "enabled": self.config.enabled,
                "threshold_tokens": self.config.threshold_tokens,
                "threshold_bytes": self.config.threshold_bytes,
                "max_preview_tokens": self.config.max_preview_tokens,
            }
        }
    
    def list_artifacts(self) -> list:
        """List all artifacts in current session."""
        return [
            {
                "id": a.artifact_id,
                "tool": a.tool_name,
                "tokens_saved": a.original_tokens - a.preview_tokens,
                "timestamp": a.timestamp,
            }
            for a in self._artifacts.values()
        ]
