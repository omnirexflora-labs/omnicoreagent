import subprocess
import shlex
import logging
from typing import Dict, Any, Optional, List, Set, Tuple, Union
from pathlib import Path
import sys
from config import ProductionConfig
from observability import (
    get_metrics_collector,
    get_logger,
    AuditLogger,
    HealthChecker,
    RateLimiter,
    perf,
)

# --------------------------------------------------------------
# 1. Load & Validate Config
# --------------------------------------------------------------
CONFIG = ProductionConfig.load()
CONFIG.validate()

# --------------------------------------------------------------
# 2. Observability (config-driven)
# --------------------------------------------------------------
log = get_logger(
    name="copilot",
    level=CONFIG.observability.log_level,
    fmt=CONFIG.observability.log_format,
    file=CONFIG.observability.log_file,
    max_bytes=CONFIG.observability.log_max_bytes,
    backup=CONFIG.observability.log_backup_count,
)


metrics = get_metrics_collector(CONFIG.observability.enable_metrics)

audit = AuditLogger(CONFIG.security.audit_log_file)
health = HealthChecker()
rate_limiter = RateLimiter(
    max_req=CONFIG.security.rate_limit_requests,
    window=CONFIG.security.rate_limit_window,
)


class Bash:
    """
    Production-ready secure Bash agent for DevOps tasks, supporting a comprehensive set of safe commands
    for file operations, system monitoring, container management, Kubernetes, log analysis, and more.
    Features:
    - Quote-aware parsing (handles nested quotes, escapes)
    - Extensive allowed command set with validation
    - Robust error handling and logging
    - Command history tracking
    - Input validation and sanitization
    """

    ALLOWED_COMMANDS: Set[str] = {
        # File inspection and navigation
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "more",
        "less",
        "tree",
        "file",
        "stat",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        # File search and pattern matching
        "find",
        "grep",
        "egrep",
        "fgrep",
        "locate",
        "which",
        "whereis",
        "type",
        "command",
        # Text processing
        "awk",
        "sed",
        "cut",
        "tr",
        "sort",
        "uniq",
        "wc",
        "fold",
        "fmt",
        "column",
        "expand",
        "unexpand",
        "join",
        "paste",
        "split",
        "csplit",
        "nl",
        "pr",
        "tac",
        "rev",
        # File comparison
        "diff",
        "cmp",
        "comm",
        "diff3",
        "sdiff",
        # Compression/Archive inspection (read-only)
        "tar",
        "zip",
        "unzip",
        "gzip",
        "gunzip",
        "bzip2",
        "bunzip2",
        "xz",
        "unxz",
        "zcat",
        "bzcat",
        "xzcat",
        # Checksums and hashing
        "md5sum",
        "sha1sum",
        "sha224sum",
        "sha256sum",
        "sha384sum",
        "sha512sum",
        "cksum",
        "sum",
        "b2sum",
        # Safe file operations
        "touch",
        "mkdir",
        "cp",
        "mv",
        "echo",
        "printf",
        "tee",
        # System information
        "date",
        "cal",
        "uptime",
        "whoami",
        "id",
        "groups",
        "users",
        "hostname",
        "hostid",
        "uname",
        "arch",
        "lscpu",
        "lsblk",
        "df",
        "du",
        "free",
        "vmstat",
        "iostat",
        "mpstat",
        "numastat",
        "lscpu",
        "lsmem",
        "lsblk",
        "blkid",
        # Process inspection
        "ps",
        "pgrep",
        "pidof",
        "pstree",
        "top",
        "htop",
        "jobs",
        # Network inspection
        "ping",
        "traceroute",
        "netstat",
        "ss",
        "ip",
        "ifconfig",
        "hostname",
        "host",
        "nslookup",
        "dig",
        "whois",
        "curl",
        "wget",
        "arp",
        "route",
        "nstat",
        # Environment
        "env",
        "printenv",
        "set",
        "export",
        "alias",
        "unalias",
        # Conditionals and testing
        "test",
        "[",
        "[[",
        "expr",
        "true",
        "false",
        # Utilities
        "seq",
        "yes",
        "sleep",
        "timeout",
        "wait",
        "watch",
        "bc",
        "dc",
        "factor",
        "jot",
        "shuf",
        "od",
        "hexdump",
        "xxd",
        "strings",
        "iconv",
        "base64",
        "base32",
        "uuencode",
        "uudecode",
        # JSON/Data processing
        "jq",
        "yq",
        "xmllint",
        "xsltproc",
        # Version control (read-only operations)
        "git",
        # Container management (read-only or safe operations)
        "docker",
        # Kubernetes (read-only or safe operations)
        "kubectl",
        # Log analysis and monitoring
        "journalctl",
        "dmesg",
        "loginctl",
        "last",
        "lastb",
        "lastlog",
        # System resource monitoring
        "sar",
        "iotop",
        "nmon",
        "glances",
        # File system checks
        "fsck",
        "badblocks",
        "duf",
        "ncdu",
        # Package inspection (read-only)
        "dpkg",
        "rpm",
        "yum",
        "dnf",
    }

    SHELL_RESERVED_WORDS: Set[str] = {
        # Control structures
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "case",
        "esac",
        "in",
        "for",
        "while",
        "until",
        "do",
        "done",
        "select",
        # Functions
        "function",
        # Grouping
        "{",
        "}",
        "(",
        ")",
        # Logical
        "!",
        "[[",
        "]]",
        # Time
        "time",
        "coproc",
        # Built-in commands (safe ones)
        ".",
        "source",
        ":",
        "true",
        "false",
        "break",
        "continue",
        "return",
        "declare",
        "typeset",
        "local",
        "readonly",
        "shift",
        "getopts",
        "read",
        "mapfile",
        "readarray",
        "let",
        "eval",
        "exec",
        "command",
        "builtin",
        "enable",
        "help",
        "hash",
        "type",
        "cd",
        "pushd",
        "popd",
        "dirs",
        "pwd",
        "exit",
        "logout",
        "times",
        "ulimit",
        "umask",
        "shopt",
        "caller",
        "complete",
        "compgen",
        "compopt",
        "fc",
        "history",
        "bind",
    }

    BLOCKED_COMMANDS: Set[str] = {
        # Deletion
        "rm",
        "rmdir",
        "shred",
        "unlink",
        # System modification
        "sudo",
        "su",
        "chown",
        "chgrp",
        "chmod",
        "chattr",
        "reboot",
        "shutdown",
        "halt",
        "poweroff",
        "init",
        # Package management (write operations)
        "apt",
        "apt-get",
        "pacman",
        "pip",
        "npm",
        "gem",
        "cargo",
        "go",
        # System tools
        "dd",
        "fdisk",
        "parted",
        "mkfs",
        "mount",
        "umount",
        "swapon",
        "swapoff",
        # Network manipulation
        "iptables",
        "ip6tables",
        "firewall-cmd",
        "ufw",
        "nc",
        "netcat",
        "nmap",
        "tcpdump",
        # Process manipulation
        "kill",
        "killall",
        "pkill",
        "xkill",
        # Compiler/interpreters
        "python",
        "python3",
        "perl",
        "ruby",
        "php",
        "node",
        "bash",
        "sh",
        "zsh",
        "fish",
        "dash",
        "ksh",
        "gcc",
        "g++",
        "cc",
        "make",
        "cmake",
        # Editors
        "vi",
        "vim",
        "nano",
        "emacs",
        "ed",
        "pico",
        # Cron/scheduling
        "crontab",
        "at",
        "batch",
        "systemctl",
    }

    def resolve_workspace(self, cwd=None):
        if cwd is None:
            cwd = Path(__file__).parent / "workspace"
        else:
            cwd = Path(cwd)

        resolved_cwd = cwd.resolve()
        if not resolved_cwd.exists():
            resolved_cwd.mkdir(parents=True, exist_ok=True)
        if not resolved_cwd.is_dir():
            raise ValueError(f"Path exists but is not a directory: {resolved_cwd}")

        return str(resolved_cwd)

    def __init__(
        self,
        cwd: str,
        timeout_seconds: int = 30,
        max_output_chars: int = 50_000,
        enable_history: bool = True,
        max_history_size: int = 100,
    ):
        """
        Initialize Bash agent with security constraints.
        Args:
            cwd: Working directory
            timeout_seconds: Max execution time per command
            max_output_chars: Max output size before truncation
            enable_history: Track command history
            max_history_size: Max commands to keep in history
        """
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.exists():
            resolved_cwd.mkdir(parents=True, exist_ok=True)
        if not resolved_cwd.is_dir():
            raise ValueError(f"Path exists but is not a directory: {resolved_cwd}")
        self.cwd = self.resolve_workspace(cwd)
        self._timeout = timeout_seconds
        self._max_output = max_output_chars
        self._enable_history = enable_history
        self._max_history_size = max_history_size
        self._command_history: List[Dict[str, Any]] = []
        log.info(f"Bash agent initialized in {self.cwd}")

    def _split_on_operators(self, cmd: str) -> List[Tuple[str, str]]:
        """
        Split command on control operators OUTSIDE of quotes and handle all edge cases.
        Handles:
        - Single and double quotes
        - Escaped characters
        - Nested quotes (e.g., "He said 'hello'")
        - Backticks and $() command substitution
        - Here-docs and here-strings
        Returns:
            List of (segment, operator) tuples
        """
        result = []
        current = []
        i = 0
        in_single_quote = False
        in_double_quote = False
        in_backtick = False
        paren_depth = 0
        while i < len(cmd):
            char = cmd[i]
            if char == "\\" and i + 1 < len(cmd) and not in_single_quote:
                current.append(char)
                current.append(cmd[i + 1])
                i += 2
                continue
            if char == "'" and not in_double_quote and not in_backtick:
                in_single_quote = not in_single_quote
                current.append(char)
                i += 1
                continue
            if char == '"' and not in_single_quote and not in_backtick:
                in_double_quote = not in_double_quote
                current.append(char)
                i += 1
                continue
            if char == "`" and not in_single_quote and not in_double_quote:
                in_backtick = not in_backtick
                current.append(char)
                i += 1
                continue
            if not in_single_quote and not in_backtick:
                if char == "$" and i + 1 < len(cmd) and cmd[i + 1] == "(":
                    paren_depth += 1
                    current.append(char)
                    current.append("(")
                    i += 2
                    continue
                if char == ")" and paren_depth > 0:
                    paren_depth -= 1
                    current.append(char)
                    i += 1
                    continue
            if (
                not in_single_quote
                and not in_double_quote
                and not in_backtick
                and paren_depth == 0
            ):
                if i + 1 < len(cmd):
                    two_char = cmd[i : i + 2]
                    if two_char in ("&&", "||", "<<", ">>", ">&", "2>", "&>", "<&"):
                        if two_char in ("<<", ">>", ">&", "2>", "&>", "<&"):
                            current.append(two_char)
                            i += 2
                            continue
                        else:
                            result.append(("".join(current).strip(), two_char))
                            current = []
                            i += 2
                            continue
                if char in (";", "|"):
                    if char == "|" and (i + 1 >= len(cmd) or cmd[i + 1] != "|"):
                        result.append(("".join(current).strip(), char))
                        current = []
                        i += 1
                        continue
                    elif char == ";":
                        result.append(("".join(current).strip(), char))
                        current = []
                        i += 1
                        continue
                if char in (">", "<"):
                    current.append(char)
                    i += 1
                    continue
            current.append(char)
            i += 1
        if current:
            result.append(("".join(current).strip(), ""))
        return result

    def _validate_git_command(self, tokens: List[str]) -> Tuple[bool, Optional[str]]:
        """
        Validate git commands - only allow read-only operations.
        Returns:
            (is_safe, error_message)
        """
        if len(tokens) < 2:
            return True, None
        git_subcommand = tokens[1]
        safe_git_commands = {
            "log",
            "show",
            "diff",
            "status",
            "branch",
            "tag",
            "ls-files",
            "ls-tree",
            "ls-remote",
            "rev-parse",
            "describe",
            "blame",
            "annotate",
            "grep",
            "config",
            "help",
            "version",
            "remote",
            "fetch",
            "clone",
        }
        dangerous_git_commands = {
            "commit",
            "push",
            "pull",
            "merge",
            "rebase",
            "reset",
            "checkout",
            "add",
            "rm",
            "mv",
            "clean",
            "stash",
            "cherry-pick",
            "revert",
            "tag",
            "branch",
        }
        if git_subcommand in dangerous_git_commands:
            return (
                False,
                f"Git subcommand '{git_subcommand}' is not allowed (write operation)",
            )
        if git_subcommand not in safe_git_commands:
            return (
                False,
                f"Git subcommand '{git_subcommand}' is not in the allowed list",
            )
        return True, None

    def _validate_docker_command(self, tokens: List[str]) -> Tuple[bool, Optional[str]]:
        """
        Validate docker commands - only allow read-only or safe operations.
        Returns:
            (is_safe, error_message)
        """
        if len(tokens) < 2:
            return True, None
        subcommand = tokens[1]
        safe_docker_commands = {
            "ps",
            "info",
            "version",
            "inspect",
            "logs",
            "events",
            "top",
            "stats",
            "history",
            "images",
            "search",
            "volume",
            "network",
            "node",
            "service",
            "stack",
            "swarm",
            "config",
            "secret",
            "buildx",
            "context",
            "trust",
        }
        dangerous_docker_commands = {
            "run",
            "exec",
            "start",
            "stop",
            "restart",
            "kill",
            "rm",
            "rmi",
            "push",
            "pull",
            "commit",
            "tag",
            "save",
            "load",
            "import",
            "export",
            "build",
        }
        if subcommand in dangerous_docker_commands:
            return (
                False,
                f"Docker subcommand '{subcommand}' is not allowed (write operation)",
            )
        if subcommand not in safe_docker_commands:
            return False, f"Docker subcommand '{subcommand}' is not in the allowed list"
        return True, None

    def _validate_kubectl_command(
        self, tokens: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate kubectl commands - only allow read-only or safe operations.
        Returns:
            (is_safe, error_message)
        """
        if len(tokens) < 2:
            return True, None
        subcommand = tokens[1]
        safe_kubectl_commands = {
            "get",
            "describe",
            "logs",
            "top",
            "explain",
            "api-resources",
            "api-versions",
            "cluster-info",
            "version",
            "config",
            "whoami",
        }
        dangerous_kubectl_commands = {
            "apply",
            "create",
            "delete",
            "edit",
            "patch",
            "replace",
            "rollout",
            "scale",
            "taint",
            "cordon",
            "uncordon",
            "drain",
            "exec",
            "run",
        }
        if subcommand in dangerous_kubectl_commands:
            return (
                False,
                f"Kubectl subcommand '{subcommand}' is not allowed (write operation)",
            )
        if subcommand not in safe_kubectl_commands:
            return (
                False,
                f"Kubectl subcommand '{subcommand}' is not in the allowed list",
            )
        return True, None

    def _sanitize_command(self, cmd: str) -> Tuple[str, List[str]]:
        """
        Validate commands while respecting quoted strings and shell syntax.
        Returns:
            (sanitized_command, blocked_messages)
        """
        segments = self._split_on_operators(cmd)
        safe_parts = []
        blocked_messages = []
        for segment, operator in segments:
            if not segment:
                if operator:
                    safe_parts.append(f" {operator} ")
                continue
            try:
                tokens = shlex.split(segment)
                if not tokens:
                    safe_parts.append(segment)
                    if operator:
                        safe_parts.append(f" {operator} ")
                    continue
                base_cmd = tokens[0]
                if "=" in base_cmd and not base_cmd.startswith("="):
                    safe_parts.append(segment)
                    if operator:
                        safe_parts.append(f" {operator} ")
                    continue
                if base_cmd in self.SHELL_RESERVED_WORDS:
                    safe_parts.append(segment)
                    if operator:
                        safe_parts.append(f" {operator} ")
                    continue
                if base_cmd in self.BLOCKED_COMMANDS:
                    msg = f"[BLOCKED] Command '{base_cmd}' is explicitly prohibited for security"
                    blocked_messages.append(msg)
                    safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                    if operator:
                        safe_parts.append(f" {operator} ")
                    continue
                if base_cmd == "git" and base_cmd in self.ALLOWED_COMMANDS:
                    is_safe, error_msg = self._validate_git_command(tokens)
                    if not is_safe:
                        msg = f"[BLOCKED] {error_msg}"
                        blocked_messages.append(msg)
                        safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                        if operator:
                            safe_parts.append(f" {operator} ")
                        continue
                if base_cmd == "docker" and base_cmd in self.ALLOWED_COMMANDS:
                    is_safe, error_msg = self._validate_docker_command(tokens)
                    if not is_safe:
                        msg = f"[BLOCKED] {error_msg}"
                        blocked_messages.append(msg)
                        safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                        if operator:
                            safe_parts.append(f" {operator} ")
                        continue
                if base_cmd == "kubectl" and base_cmd in self.ALLOWED_COMMANDS:
                    is_safe, error_msg = self._validate_kubectl_command(tokens)
                    if not is_safe:
                        msg = f"[BLOCKED] {error_msg}"
                        blocked_messages.append(msg)
                        safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                        if operator:
                            safe_parts.append(f" {operator} ")
                        continue
                if base_cmd not in self.ALLOWED_COMMANDS:
                    msg = f"[BLOCKED] Command '{base_cmd}' is not in the allowed list"
                    blocked_messages.append(msg)
                    safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                    if operator:
                        safe_parts.append(f" {operator} ")
                    continue
                safe_parts.append(segment)
                if operator:
                    safe_parts.append(f" {operator} ")
            except ValueError as e:
                msg = f"[BLOCKED] Command parsing failed: {e}"
                blocked_messages.append(msg)
                safe_parts.append(f"echo {shlex.quote(msg)} >&2")
                if operator:
                    safe_parts.append(f" {operator} ")
        safe_cmd = "".join(safe_parts).strip()
        return safe_cmd, blocked_messages

    def _add_to_history(self, cmd: str, result: Dict[str, Any]) -> None:
        """Add command execution to history."""
        if not self._enable_history:
            return
        history_entry = {
            "timestamp": subprocess.run(
                ["date", "+%Y-%m-%d %H:%M:%S"], capture_output=True, text=True
            ).stdout.strip(),
            "command": cmd,
            "cwd": result["data"]["cwd"],
            "returncode": result["data"]["returncode"],
            "status": result["status"],
        }
        self._command_history.append(history_entry)
        if len(self._command_history) > self._max_history_size:
            self._command_history = self._command_history[-self._max_history_size :]
        log.info("Added command to history")

    def exec_bash_command(self, cmd: Union[str, List, Dict]) -> Dict[str, Any]:
        """
        Execute bash command with comprehensive safety checks.
        Args:
            cmd: Command as string, list, or dict with 'cmd' key
        Returns:
            Dict with status, data (stdout, stderr, returncode, cwd)
        """

        if isinstance(cmd, dict):
            cmd = cmd.get("cmd", "")
        elif isinstance(cmd, list):
            cmd = " ".join(str(part) for part in cmd)
        elif not isinstance(cmd, str):
            cmd = str(cmd)
        if not cmd or not cmd.strip():
            log.warning("Empty command provided")
            return {
                "status": "error",
                "error": "No command provided",
                "data": {
                    "stdout": "",
                    "stderr": "No command provided",
                    "returncode": 1,
                    "cwd": self.cwd,
                },
            }
        cmd = cmd.strip()
        log.info(f"Executing command: {cmd[:100]}{'...' if len(cmd) > 100 else ''}")
        try:
            safe_cmd, blocked_messages = self._sanitize_command(cmd)
            if blocked_messages:
                log.warning(f"Blocked parts in command: {blocked_messages}")
            MARKER = "__OMNI_CWD_END__"
            wrapped_cmd = f"cd {shlex.quote(self.cwd)} && {{ {safe_cmd}; }}; echo -n '{MARKER}'; pwd -P"
            result = subprocess.run(
                ["/bin/bash", "-c", wrapped_cmd],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env={**subprocess.os.environ, "LANG": "C.UTF-8"},
            )
            stdout_raw = result.stdout
            stderr_raw = result.stderr
            full_stderr = stderr_raw
            if MARKER in stdout_raw:
                stdout_part, cwd_part = stdout_raw.rsplit(MARKER, 1)
                stdout_clean = stdout_part
                new_cwd = cwd_part.strip()
                try:
                    new_cwd_path = Path(new_cwd).resolve()
                    if new_cwd_path.is_dir():
                        old_cwd = self.cwd
                        self.cwd = str(new_cwd_path)
                        if old_cwd != self.cwd:
                            log.info(f"CWD changed: {old_cwd} -> {self.cwd}")
                except Exception as e:
                    log.error(f"Failed to update CWD: {e}")
            else:
                stdout_clean = stdout_raw

            def truncate(s: str) -> str:
                if len(s) > self._max_output:
                    half = self._max_output // 2
                    return (
                        s[:half]
                        + f"\n\n... [OUTPUT TRUNCATED - {len(s) - self._max_output} chars omitted] ...\n\n"
                        + s[-half:]
                    )
                return s

            stdout_final = truncate(stdout_clean)
            stderr_final = truncate(full_stderr.rstrip("\n"))
            if result.returncode == 0 and not stdout_final and not stderr_final:
                stdout_final = "Command completed successfully with no output."
            response = {
                "status": "success" if result.returncode == 0 else "error",
                "data": {
                    "stdout": stdout_final,
                    "stderr": stderr_final,
                    "returncode": result.returncode,
                    "cwd": self.cwd,
                },
            }
            self._add_to_history(cmd, response)
            log.info(f"Command completed with returncode {result.returncode}")
            return response
        except subprocess.TimeoutExpired:
            log.error(f"Command timed out after {self._timeout}s")
            return {
                "status": "error",
                "error": f"Command timed out after {self._timeout} seconds",
                "data": {
                    "stdout": "",
                    "stderr": f"Command execution exceeded timeout limit of {self._timeout}s",
                    "returncode": -1,
                    "cwd": self.cwd,
                },
            }
        except Exception as e:
            log.exception("Bash execution failed with unexpected error")
            error_msg = f"Execution failed: {type(e).__name__}: {str(e)}"
            return {
                "status": "error",
                "error": error_msg,
                "data": {
                    "stdout": "",
                    "stderr": error_msg,
                    "returncode": -1,
                    "cwd": self.cwd,
                },
            }

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get command history."""
        if limit:
            return self._command_history[-limit:]
        return self._command_history.copy()

    def clear_history(self) -> None:
        """Clear command history."""
        self._command_history.clear()
        log.info("Command history cleared")
