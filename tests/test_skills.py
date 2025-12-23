"""
Tests for Agent Skills module.
"""

import sys
import json
import tempfile
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from omnicoreagent.core.skills.models import SkillMetadata
from omnicoreagent.core.skills.manager import SkillManager
from omnicoreagent.core.skills.tools import build_skill_tools
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry


class TestSkillMetadata:
    """Tests for SkillMetadata model."""

    def test_valid_skill_name(self):
        """Test valid skill names pass validation."""
        valid_names = ["my-skill", "skill123", "a", "my-cool-skill"]
        for name in valid_names:
            metadata = SkillMetadata(
                name=name, description="Test skill", path=Path("/tmp/test")
            )
            assert metadata.name == name

    def test_invalid_skill_name_uppercase(self):
        """Test uppercase characters are rejected."""
        with pytest.raises(ValueError, match="lowercase"):
            SkillMetadata(name="MySkill", description="Test", path=Path("/tmp"))

    def test_invalid_skill_name_starts_with_hyphen(self):
        """Test names starting with hyphen are rejected."""
        with pytest.raises(ValueError, match="start or end"):
            SkillMetadata(name="-skill", description="Test", path=Path("/tmp"))

    def test_invalid_skill_name_consecutive_hyphens(self):
        """Test consecutive hyphens are rejected."""
        with pytest.raises(ValueError, match="consecutive"):
            SkillMetadata(name="my--skill", description="Test", path=Path("/tmp"))


class TestSkillManager:
    """Tests for SkillManager."""

    def setup_method(self):
        """Create a temporary skills directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.skills_root = Path(self.temp_dir) / "skills"
        self.skills_root.mkdir()

    def _create_skill(self, name: str, description: str = "Test skill"):
        """Helper to create a test skill."""
        skill_dir = self.skills_root / name
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(f"""---
name: {name}
description: {description}
---

# {name}
Test skill body.
""")
        return skill_dir

    def test_discover_skills(self):
        """Test skill discovery."""
        self._create_skill("test-skill", "A test skill")

        manager = SkillManager(self.skills_root)
        skills = manager.discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "test-skill"
        assert skills[0].description == "A test skill"

    def test_discover_multiple_skills(self):
        """Test discovering multiple skills."""
        self._create_skill("skill-one", "First skill")
        self._create_skill("skill-two", "Second skill")

        manager = SkillManager(self.skills_root)
        skills = manager.discover_skills()

        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-one", "skill-two"}

    def test_skip_directory_without_skill_md(self):
        """Test directories without SKILL.md are skipped."""
        self._create_skill("valid-skill")
        (self.skills_root / "no-skill-file").mkdir()

        manager = SkillManager(self.skills_root)
        skills = manager.discover_skills()

        assert len(skills) == 1
        assert skills[0].name == "valid-skill"

    def test_validate_skill_success(self):
        """Test successful skill validation."""
        self._create_skill("my-skill")

        manager = SkillManager(self.skills_root)
        manager.discover_skills()

        path = manager.validate_skill("my-skill")
        assert path.exists()
        assert path.name == "my-skill"

    def test_validate_skill_not_found(self):
        """Test validation fails for non-existent skill."""
        manager = SkillManager(self.skills_root)

        with pytest.raises(RuntimeError, match="not found"):
            manager.validate_skill("nonexistent")

    def test_get_skills_context_xml(self):
        """Test XML context generation."""
        self._create_skill("test-skill", "Test description")

        manager = SkillManager(self.skills_root)
        manager.discover_skills()

        xml = manager.get_skills_context_xml()

        assert "<available_skills>" in xml
        assert "<name>test-skill</name>" in xml
        assert "<description>Test description</description>" in xml
        assert "SKILL.md</location>" in xml

    def test_parse_yaml_complex(self):
        """Test parsing more complex frontmatter with metadata and allowed-tools."""
        skill_dir = self.skills_root / "complex-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: complex-skill
description: "A skill with complex metadata: and colons"
allowed-tools: Bash(git:*) Bash(jq:*)
metadata:
  author: AI
  version: "2.0"
  nested-key: with-dash
---
""")
        manager = SkillManager(self.skills_root)
        manager.discover_skills()
        skill = manager.get_skill("complex-skill")

        assert skill is not None
        assert skill.name == "complex-skill"
        assert skill.description == "A skill with complex metadata: and colons"
        assert skill.allowed_tools == ["Bash(git:*)", "Bash(jq:*)"]
        assert skill.metadata["author"] == "AI"
        assert skill.metadata["version"] == "2.0"
        assert skill.metadata["nested-key"] == "with-dash"


class TestSkillTools:
    """Tests for skill tools."""

    def setup_method(self):
        """Create a temporary skills directory with test skill."""
        self.temp_dir = tempfile.mkdtemp()
        self.skills_root = Path(self.temp_dir) / "skills"
        self.skills_root.mkdir()

        # Create test skill
        self.skill_dir = self.skills_root / "test-skill"
        self.skill_dir.mkdir()
        (self.skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill
---
# Test Skill
Body content.
""")

        # Create scripts directory with test script
        scripts_dir = self.skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "echo.py").write_text("""
import sys
import json
print(json.dumps({"args": sys.argv[1:]}))
""")

        self.manager = SkillManager(self.skills_root)
        self.manager.discover_skills()

        # Create a ToolRegistry and register skill tools
        self.registry = ToolRegistry()
        build_skill_tools(self.manager, self.registry)

    def test_read_skill_file_success(self):
        """Test reading a skill file."""
        tool = self.registry.get_tool("read_skill_file")
        result = tool.function("test-skill", "SKILL.md")

        assert result["status"] == "success"
        assert "name: test-skill" in result["data"]

    def test_read_skill_file_not_found(self):
        """Test reading non-existent file."""
        tool = self.registry.get_tool("read_skill_file")
        result = tool.function("test-skill", "nonexistent.md")

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_read_skill_file_path_escape(self):
        """Test path escape is blocked."""
        tool = self.registry.get_tool("read_skill_file")
        result = tool.function("test-skill", "../../../etc/passwd")

        assert result["status"] == "error"
        assert (
            "outside" in result["message"].lower()
            or "not found" in result["message"].lower()
        )

    def test_run_skill_script_success(self):
        """Test running a skill script."""
        tool = self.registry.get_tool("run_skill_script")
        result = tool.function("test-skill", "echo.py", ["hello", "world"])

        assert result["status"] == "success"
        output = json.loads(result["data"]["stdout"])
        assert output["args"] == ["hello", "world"]

    def test_run_skill_script_not_found(self):
        """Test running non-existent script."""
        tool = self.registry.get_tool("run_skill_script")
        result = tool.function("test-skill", "nonexistent.py")

        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_run_skill_script_js_success(self):
        """Test running a JavaScript script."""
        scripts_dir = self.skill_dir / "scripts"
        js_path = scripts_dir / "hello.js"
        js_path.write_text("console.log('Hello from Node ' + process.argv[2]);\n")
        js_path.chmod(0o755)

        tool = self.registry.get_tool("run_skill_script")
        result = tool.function("test-skill", "hello.js", ["World"])

        if result["status"] == "success":
            assert "Hello from Node World" in result["data"]["stdout"]
        else:
            # If node is missing (unlikely given 'which' results), it should be a found error
            assert "not found" in result["message"].lower()

    def test_run_skill_script_perl_success(self):
        """Test running a Perl script."""
        scripts_dir = self.skill_dir / "scripts"
        pl_path = scripts_dir / "hello.pl"
        pl_path.write_text('print "Hello from Perl $ARGV[0]\\n";\n')
        pl_path.chmod(0o755)

        tool = self.registry.get_tool("run_skill_script")
        result = tool.function("test-skill", "hello.pl", ["World"])

        if result["status"] == "success":
            assert "Hello from Perl World" in result["data"]["stdout"]
        else:
            assert "not found" in result["message"].lower()

    def test_run_skill_script_shebang_success(self):
        """Test running a script via shebang (fallback)."""
        scripts_dir = self.skill_dir / "scripts"
        script_path = scripts_dir / "custom"
        # Use bash as shebang even without extension
        script_path.write_text('#!/bin/bash\necho "Hello from Shebang $1"\n')
        script_path.chmod(0o755)

        tool = self.registry.get_tool("run_skill_script")
        result = tool.function("test-skill", "custom", ["World"])

        if result["status"] == "success":
            assert "Hello from Shebang World" in result["data"]["stdout"]
        else:
            assert "not found" in result["message"].lower()

    def test_run_skill_script_dispatcher_logic(self):
        """Test the dispatcher logic for various extensions using mocking."""
        scripts_dir = self.skill_dir / "scripts"
        tool = self.registry.get_tool("run_skill_script")

        # Extensions to test: (extension, expected_prefix)
        extensions = [
            (".py", [sys.executable]),
            (".sh", ["bash"]),
            (".js", ["node"]),
            (".mjs", ["node"]),
            (".cjs", ["node"]),
            (".ts", ["ts-node"]),
            (".rb", ["ruby"]),
            (".pl", ["perl"]),
            (".php", []),  # Fallback
        ]

        for ext, prefix in extensions:
            script_name = f"test{ext}"
            script_path = scripts_dir / script_name
            script_path.write_text("dummy")

            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

                tool.function("test-skill", script_name, ["arg1"])

                # Verify the command built
                expected_cmd = prefix + [str(script_path.resolve()), "arg1"]
                mock_run.assert_called_once()
                actual_cmd = mock_run.call_args[0][0]
                assert actual_cmd == expected_cmd
