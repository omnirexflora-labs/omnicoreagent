"""
Agent Skills Module

Provides support for loading and executing Agent Skills following the
agentskills.io specification.

Skills are reusable capability packages that give agents specialized knowledge
and executable scripts. Each skill lives in a directory with a SKILL.md file.
"""

from omnicoreagent.core.skills.models import SkillMetadata
from omnicoreagent.core.skills.manager import SkillManager
from omnicoreagent.core.skills.tools import build_skill_tools

__all__ = ["SkillMetadata", "SkillManager", "build_skill_tools"]
