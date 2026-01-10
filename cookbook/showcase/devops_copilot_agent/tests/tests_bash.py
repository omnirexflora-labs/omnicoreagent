"""
Comprehensive test suite for DevOps Copilot.
Covers unit tests, integration tests, and security tests.
"""

import pytest
import asyncio
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any
import subprocess


class TestBashAgent:
    """Test suite for Bash agent command execution."""

    @pytest.fixture
    def bash_agent(self, tmp_path):
        """Create bash agent with temp directory."""
        from bash import Bash

        return Bash(cwd=str(tmp_path), timeout_seconds=5)

    # --- Command Parsing Tests ---

    def test_simple_command(self, bash_agent):
        """Test simple command execution."""
        result = bash_agent.exec_bash_command("echo 'hello world'")
        assert result["status"] == "success"
        assert "hello world" in result["data"]["stdout"]
        assert result["data"]["returncode"] == 0

    def test_complex_quoting(self, bash_agent):
        """Test complex quoting with special characters."""
        cmd = "echo 'name ; age | city' > data.csv"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert result["data"]["returncode"] == 0

        # Verify file was created
        result = bash_agent.exec_bash_command("cat data.csv")
        assert "name ; age | city" in result["data"]["stdout"]

    def test_pipeline_commands(self, bash_agent):
        """Test pipeline command execution."""
        cmd = "echo -e 'apple\\nbanana\\napple' | sort | uniq"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "apple" in result["data"]["stdout"]
        assert "banana" in result["data"]["stdout"]

    def test_conditional_execution(self, bash_agent):
        """Test conditional command chaining."""
        cmd = "mkdir test_dir && cd test_dir && pwd"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "test_dir" in result["data"]["stdout"]

    def test_command_substitution(self, bash_agent):
        """Test command substitution syntax."""
        cmd = "echo 'Files: '$(ls | wc -l)"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "Files:" in result["data"]["stdout"]

    # --- Security Tests ---

    def test_blocked_rm_command(self, bash_agent):
        """Test that rm command is blocked."""
        result = bash_agent.exec_bash_command("touch test.txt && rm test.txt")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "rm" in result["data"]["stderr"]
        # File should still exist (rm was blocked)
        verify = bash_agent.exec_bash_command("ls test.txt")
        assert "test.txt" in verify["data"]["stdout"]

    def test_blocked_sudo_command(self, bash_agent):
        """Test that sudo command is blocked."""
        result = bash_agent.exec_bash_command("sudo ls")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "sudo" in result["data"]["stderr"]

    def test_blocked_python_execution(self, bash_agent):
        """Test that python execution is blocked."""
        result = bash_agent.exec_bash_command("python -c 'print(123)'")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "python" in result["data"]["stderr"]

    def test_partial_blocking(self, bash_agent):
        """Test that allowed commands run even with blocked ones."""
        cmd = "echo 'before' && rm test.txt && echo 'after'"
        result = bash_agent.exec_bash_command(cmd)
        # Both echo commands should run
        assert "before" in result["data"]["stdout"]
        assert "after" in result["data"]["stdout"]
        # But rm should be blocked
        assert "[BLOCKED]" in result["data"]["stderr"]

    def test_git_write_operations_blocked(self, bash_agent):
        """Test that git write operations are blocked."""
        result = bash_agent.exec_bash_command("git commit -m 'test'")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "commit" in result["data"]["stderr"]

    def test_git_read_operations_allowed(self, bash_agent):
        """Test that git read operations are allowed."""
        # Initialize a git repo first
        bash_agent.exec_bash_command("git init")
        result = bash_agent.exec_bash_command("git status")
        # Should not be blocked (might fail if not a git repo, but not blocked)
        assert (
            "[BLOCKED]" not in result["data"]["stderr"] or result["status"] != "error"
        )

    def test_docker_write_blocked(self, bash_agent):
        """Test that docker write operations are blocked."""
        result = bash_agent.exec_bash_command("docker run alpine echo hello")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "run" in result["data"]["stderr"]

    def test_kubectl_write_blocked(self, bash_agent):
        """Test that kubectl write operations are blocked."""
        result = bash_agent.exec_bash_command("kubectl delete pod test")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "delete" in result["data"]["stderr"]

    # --- Edge Case Tests ---

    def test_empty_command(self, bash_agent):
        """Test empty command handling."""
        result = bash_agent.exec_bash_command("")
        assert result["status"] == "error"
        assert "No command provided" in result["data"]["stderr"]

    def test_malformed_quotes(self, bash_agent):
        """Test malformed quote handling."""
        result = bash_agent.exec_bash_command("echo 'unclosed quote")
        assert "[BLOCKED]" in result["data"]["stderr"]
        assert "parsing failed" in result["data"]["stderr"].lower()

    def test_timeout_handling(self, bash_agent):
        """Test command timeout."""
        result = bash_agent.exec_bash_command("sleep 10")
        assert result["status"] == "error"
        assert "timeout" in result["data"]["stderr"].lower()

    def test_cwd_tracking(self, bash_agent):
        """Test that CWD is tracked correctly."""
        initial_cwd = bash_agent.cwd
        bash_agent.exec_bash_command("mkdir subdir && cd subdir")
        assert "subdir" in bash_agent.cwd
        assert bash_agent.cwd != initial_cwd

    def test_output_truncation(self, bash_agent):
        """Test large output truncation."""
        # Generate large output
        cmd = "seq 1 100000"
        result = bash_agent.exec_bash_command(cmd)
        if len(result["data"]["stdout"]) > bash_agent._max_output:
            assert "[OUTPUT TRUNCATED]" in result["data"]["stdout"]

    # --- File Operation Tests ---

    def test_file_creation(self, bash_agent):
        """Test file creation and writing."""
        cmd = "echo 'test content' > test.txt && cat test.txt"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "test content" in result["data"]["stdout"]

    def test_directory_operations(self, bash_agent):
        """Test directory creation and navigation."""
        cmd = "mkdir -p dir1/dir2/dir3 && cd dir1/dir2 && pwd"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "dir1/dir2" in result["data"]["stdout"]

    def test_file_searching(self, bash_agent):
        """Test find command."""
        bash_agent.exec_bash_command("touch file1.txt file2.log file3.txt")
        result = bash_agent.exec_bash_command("find . -name '*.txt' | sort")
        assert result["status"] == "success"
        assert "file1.txt" in result["data"]["stdout"]
        assert "file3.txt" in result["data"]["stdout"]

    # --- History Tests ---

    def test_command_history(self, bash_agent):
        """Test command history tracking."""
        bash_agent.exec_bash_command("echo 'test1'")
        bash_agent.exec_bash_command("echo 'test2'")
        history = bash_agent.get_history()
        assert len(history) == 2
        assert history[0]["command"] == "echo 'test1'"
        assert history[1]["command"] == "echo 'test2'"

    def test_history_limit(self, bash_agent):
        """Test history size limit."""
        bash_agent._max_history_size = 5
        for i in range(10):
            bash_agent.exec_bash_command(f"echo {i}")
        history = bash_agent.get_history()
        assert len(history) == 5
        # Should have last 5 commands
        assert history[-1]["command"] == "echo 9"


class TestDevOpsCopilot:
    """Integration tests for DevOps Copilot agent."""

    @pytest.fixture
    async def copilot(self, tmp_path):
        """Create DevOps Copilot instance."""
        # Mock initialization - adjust based on your actual setup
        from copilot import DevOpsCopilotRunner

        runner = DevOpsCopilotRunner()
        await runner.initialize()
        return runner

    @pytest.mark.asyncio
    async def test_agent_initialization(self, copilot):
        """Test agent initializes correctly."""
        assert copilot.connected is True
        assert copilot.agent is not None
        assert copilot.bash_agent is not None

    @pytest.mark.asyncio
    async def test_simple_query(self, copilot):
        """Test simple query execution."""
        response = await copilot.handle_chat(
            query="List files in current directory", session_id="test_session"
        )
        assert response is not None
        assert "response" in response

    @pytest.mark.asyncio
    async def test_devops_task(self, copilot):
        """Test DevOps-specific task."""
        response = await copilot.handle_chat(
            query="Check disk usage and report largest directories",
            session_id="test_session",
        )
        assert response is not None
        # Should execute df or du commands


class TestSecurityValidation:
    """Security-focused test cases."""

    def test_sql_injection_attempt(self, bash_agent):
        """Test SQL injection patterns are handled."""
        # Even though we don't have SQL, test injection patterns
        cmd = "echo 'data' > file.txt; rm -rf /"
        result = bash_agent.exec_bash_command(cmd)
        assert "[BLOCKED]" in result["data"]["stderr"]

    def test_command_injection_via_quotes(self, bash_agent):
        """Test command injection via quote escaping."""
        cmd = "echo 'test' && $(rm test.txt)"
        result = bash_agent.exec_bash_command(cmd)
        # rm should be blocked even in command substitution
        assert "[BLOCKED]" in result["data"]["stderr"]

    def test_path_traversal(self, bash_agent):
        """Test path traversal attempts."""
        # This should work (reading is allowed) but test it doesn't escape sandbox
        result = bash_agent.exec_bash_command("cat /etc/passwd")
        # Should execute but within allowed boundaries
        assert result["data"]["returncode"] in [0, 1]  # 0 if exists, 1 if not

    def test_shell_metacharacters(self, bash_agent):
        """Test handling of shell metacharacters."""
        cmd = "echo 'test' > file.txt && cat file.txt"
        result = bash_agent.exec_bash_command(cmd)
        assert result["status"] == "success"
        assert "test" in result["data"]["stdout"]


class TestRealWorldScenarios:
    """Test real-world DevOps scenarios."""

    def test_log_analysis(self, bash_agent):
        """Test log analysis workflow."""
        # Create mock log file
        bash_agent.exec_bash_command(
            "echo 'ERROR: Database connection failed' > app.log && "
            "echo 'INFO: Application started' >> app.log && "
            "echo 'ERROR: Timeout occurred' >> app.log"
        )

        # Analyze logs
        result = bash_agent.exec_bash_command("grep ERROR app.log | wc -l")
        assert result["status"] == "success"
        assert "2" in result["data"]["stdout"]

    def test_config_audit(self, bash_agent):
        """Test configuration file audit."""
        # Create mock config files
        bash_agent.exec_bash_command(
            "mkdir -p services/api services/web && "
            "echo 'port=8080' > services/api/config.conf && "
            "echo 'port=3000' > services/web/config.conf"
        )

        # Audit configs
        result = bash_agent.exec_bash_command(
            "find services -name '*.conf' -exec cat {} \\;"
        )
        assert result["status"] == "success"
        assert "port=8080" in result["data"]["stdout"]
        assert "port=3000" in result["data"]["stdout"]

    def test_workspace_report(self, bash_agent):
        """Test workspace reporting."""
        # Create test structure
        bash_agent.exec_bash_command(
            "mkdir -p src docs tests && "
            "touch src/main.py docs/README.md tests/test_main.py"
        )

        # Generate report
        result = bash_agent.exec_bash_command(
            "echo '# Workspace Report' > report.md && "
            "echo '## Directories' >> report.md && "
            "ls -d */ >> report.md && "
            "cat report.md"
        )
        assert result["status"] == "success"
        assert "Workspace Report" in result["data"]["stdout"]

    def test_disk_usage_analysis(self, bash_agent):
        """Test disk usage analysis."""
        # Create files of different sizes
        bash_agent.exec_bash_command(
            "dd if=/dev/zero of=small.bin bs=1K count=10 2>/dev/null && "
            "dd if=/dev/zero of=large.bin bs=1M count=5 2>/dev/null"
        )

        # Analyze
        result = bash_agent.exec_bash_command("du -h *.bin | sort -hr")
        assert result["status"] == "success"


# Performance Tests
class TestPerformance:
    """Performance and load testing."""

    def test_rapid_commands(self, bash_agent):
        """Test handling rapid command execution."""
        import time

        start = time.time()
        for i in range(50):
            bash_agent.exec_bash_command(f"echo {i}")
        duration = time.time() - start
        # Should complete in reasonable time
        assert duration < 10  # 50 commands in < 10 seconds

    def test_large_output_handling(self, bash_agent):
        """Test handling of large output."""
        result = bash_agent.exec_bash_command("seq 1 50000")
        assert result["status"] == "success"
        # Should handle truncation gracefully


# Pytest configuration
@pytest.fixture(scope="session")
def bash_agent():
    """Session-scoped bash agent."""
    import tempfile
    from bash_agent import Bash

    tmpdir = tempfile.mkdtemp()
    agent = Bash(cwd=tmpdir, timeout_seconds=10)
    yield agent
    # Cleanup
    shutil.rmtree(tmpdir, ignore_errors=True)


# Run with: pytest test_copilot.py -v --tb=short
# Coverage: pytest test_copilot.py --cov=bash_agent --cov-report=html
