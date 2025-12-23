"""
Skill Manager for discovering and loading Agent Skills.

Handles:
- Scanning skills directory for valid skills
- Parsing SKILL.md YAML frontmatter
- Runtime validation when tools access skills
- Generating XML context for agent prompts
"""

import re
from pathlib import Path
from typing import Dict, List, Optional
from omnicoreagent.core.utils import logger
from omnicoreagent.core.skills.models import SkillMetadata


class SkillManager:
    """
    Manages Agent Skills discovery, validation, and context generation.

    Usage:
        manager = SkillManager()
        skills = manager.discover_skills()
        xml_context = manager.get_skills_context_xml()
    """

    def __init__(self, skills_root: Path = Path(".agents/skills")):
        """
        Initialize the SkillManager.

        Args:
            skills_root: Root directory containing skill subdirectories.
                         Each subdirectory should contain a SKILL.md file.
        """
        self.skills_root = skills_root.resolve()
        self.skills: Dict[str, SkillMetadata] = {}

    def discover_skills(self) -> List[SkillMetadata]:
        """
        Scan the skills directory and load metadata for all valid skills.

        Returns:
            List of SkillMetadata for all discovered skills.
        """
        self.skills.clear()

        if not self.skills_root.exists():
            logger.debug(f"Skills directory not found: {self.skills_root}")
            return []

        if not self.skills_root.is_dir():
            logger.warning(f"Skills root is not a directory: {self.skills_root}")
            return []

        for skill_dir in self.skills_root.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                logger.debug(f"Skipping {skill_dir.name}: no SKILL.md found")
                continue

            try:
                metadata = self._load_skill_metadata(skill_dir)
                if metadata:
                    self.skills[metadata.name] = metadata
                    logger.info(f"Loaded skill: {metadata.name}")
            except Exception as e:
                logger.warning(f"Failed to load skill {skill_dir.name}: {e}")

        return list(self.skills.values())

    def validate_skill(self, skill_name: str) -> Path:
        """
        Validate that a skill exists and has the required SKILL.md file.

        This is called at runtime when tools access a skill.

        Args:
            skill_name: Name of the skill to validate.

        Returns:
            Resolved path to the skill directory.

        Raises:
            RuntimeError: If skill doesn't exist or is invalid.
        """
        skill_path = (self.skills_root / skill_name).resolve()

        # Security: ensure skill is within skills_root
        if not str(skill_path).startswith(str(self.skills_root)):
            raise RuntimeError(f"Invalid skill path: {skill_name}")

        if not skill_path.exists() or not skill_path.is_dir():
            raise RuntimeError(f"Skill '{skill_name}' not found")

        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            raise RuntimeError(f"Skill '{skill_name}' is missing SKILL.md")

        return skill_path

    def get_skill(self, skill_name: str) -> Optional[SkillMetadata]:
        """
        Get metadata for a specific skill.

        Args:
            skill_name: Name of the skill.

        Returns:
            SkillMetadata if found, None otherwise.
        """
        return self.skills.get(skill_name)

    def get_skills_context_xml(self) -> str:
        """
        Generate XML representation of available skills for agent context.

        This is injected into the agent's system prompt so it knows
        which skills are available.

        Returns:
            XML string with skill names and descriptions.
        """
        if not self.skills:
            return ""

        lines = ["<available_skills>"]
        for skill in self.skills.values():
            lines.append("  <skill>")
            lines.append(f"    <name>{skill.name}</name>")
            lines.append(f"    <description>{skill.description}</description>")
            lines.append(f"    <location>{skill.path}/SKILL.md</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")

        return "\n".join(lines)

    def _load_skill_metadata(self, skill_dir: Path) -> Optional[SkillMetadata]:
        """
        Load and parse the YAML frontmatter from a skill's SKILL.md file.

        Args:
            skill_dir: Path to the skill directory.

        Returns:
            SkillMetadata if parsing succeeds, None otherwise.
        """
        skill_md = skill_dir / "SKILL.md"

        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {skill_md}: {e}")
            return None

        # Parse YAML frontmatter (between --- delimiters)
        frontmatter = self._extract_frontmatter(content)
        if not frontmatter:
            logger.warning(f"No valid frontmatter in {skill_md}")
            return None

        # Parse the YAML-like frontmatter manually (simple key: value format)
        parsed = self._parse_yaml_frontmatter(frontmatter)

        if "name" not in parsed or "description" not in parsed:
            logger.warning(f"Missing required fields in {skill_md}")
            return None

        # Validate that skill directory name matches the name in frontmatter
        if parsed["name"] != skill_dir.name:
            logger.warning(
                f"Skill name mismatch: directory '{skill_dir.name}' vs "
                f"frontmatter '{parsed['name']}'"
            )
            return None

        allowed_tools_raw = parsed.get("allowed-tools")
        allowed_tools = allowed_tools_raw.split() if allowed_tools_raw else None

        try:
            return SkillMetadata(
                name=parsed["name"],
                description=parsed["description"],
                path=skill_dir.resolve(),
                license=parsed.get("license"),
                compatibility=parsed.get("compatibility"),
                metadata=parsed.get("metadata"),
                allowed_tools=allowed_tools,
            )
        except Exception as e:
            logger.warning(f"Invalid skill metadata in {skill_md}: {e}")
            return None

    def _extract_frontmatter(self, content: str) -> Optional[str]:
        """
        Extract YAML frontmatter from markdown content.

        Frontmatter is delimited by --- at start and end.
        """
        if not content.startswith("---"):
            return None

        # Find the closing ---
        end_idx = content.find("---", 3)
        if end_idx == -1:
            return None

        return content[3:end_idx].strip()

    def _parse_yaml_frontmatter(self, frontmatter: str) -> Dict:
        """
        Parse simple YAML frontmatter (key: value format).

        Handles:
        - Simple string values
        - Nested metadata objects (one level deep)
        - Keys with hyphens (e.g., allowed-tools)
        """
        result = {}
        lines = frontmatter.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            if not line:
                i += 1
                continue

            # Top-level key
            match = re.match(r"^([\w-]+):\s*(.*)$", line)
            if match:
                key, value = match.groups()
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                if key == "metadata" and not value:
                    # Parse nested metadata
                    metadata_dict = {}
                    i += 1
                    while i < len(lines):
                        next_line = lines[i]
                        if not next_line.strip():
                            i += 1
                            continue
                        if not next_line.startswith("  "):
                            # End of metadata block
                            break

                        nested_match = re.match(r"^\s+([\w-]+):\s*(.*)$", next_line)
                        if nested_match:
                            n_key, n_value = nested_match.groups()
                            metadata_dict[n_key.strip()] = (
                                n_value.strip().strip('"').strip("'")
                            )
                        i += 1
                    result["metadata"] = metadata_dict
                    continue  # We already incremented i
                else:
                    if value:
                        result[key] = value
            i += 1

        return result
