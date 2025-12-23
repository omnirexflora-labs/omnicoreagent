"""
Pydantic models for Agent Skills.

Follows the Agent Skills specification from agentskills.io.
"""

import re
from pathlib import Path
from typing import Optional, Dict, List

from pydantic import BaseModel, Field, field_validator


class SkillMetadata(BaseModel):
    """
    Represents the YAML frontmatter metadata from a SKILL.md file.

    Required fields:
        - name: 1-64 chars, lowercase alphanumeric + hyphens
        - description: 1-1024 chars, describes what skill does and when to use it

    Optional fields:
        - license: License applied to the skill
        - compatibility: Environment requirements (1-500 chars)
        - metadata: Custom key-value pairs
        - allowed_tools: Space-delimited list of pre-approved tools
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Skill identifier, must be lowercase alphanumeric with hyphens",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="What the skill does and when to use it",
    )
    path: Path = Field(..., description="Resolved path to the skill directory")
    license: Optional[str] = Field(
        default=None, description="License applied to the skill"
    )
    compatibility: Optional[str] = Field(
        default=None, max_length=500, description="Environment requirements"
    )
    metadata: Optional[Dict[str, str]] = Field(
        default=None, description="Custom key-value pairs"
    )
    allowed_tools: Optional[List[str]] = Field(
        default=None, alias="allowed-tools", description="List of pre-approved tools"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """
        Validate skill name follows the specification:
        - Lowercase alphanumeric characters and hyphens only
        - Must not start or end with hyphen
        - Must not contain consecutive hyphens
        """
        # Check for valid characters
        if not re.match(r"^[a-z0-9-]+$", v):
            raise ValueError(
                "Skill name must contain only lowercase letters, numbers, and hyphens"
            )

        # Check start/end
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("Skill name must not start or end with a hyphen")

        # Check consecutive hyphens
        if "--" in v:
            raise ValueError("Skill name must not contain consecutive hyphens")

        return v

    model_config = {"frozen": False, "populate_by_name": True}
