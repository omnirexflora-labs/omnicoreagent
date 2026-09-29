"""C5: the parser agrees with a real shell.

A bypass is a command the policy reads one way and the shell runs another. So
each command below is run by a real `sh` in a temporary directory whose PATH
holds only stand-ins: every program just logs its own name (shells stay real,
so `sh -c '...'` really runs what is inside). Whatever the shell really ran
must be among the programs the parser reported, or the parser must have called
the command opaque. No command here names an absolute path or leaves the
temporary directory.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from omnicoreagent.governance.commands import parse_command

STANDINS = ["rm", "git", "ls", "cat", "head", "wc", "grep", "xargs", "find", "sudo", "timeout",
            "nohup", "env", "date", "make", "base64", "curl", "touch", "true", "sed", "sort", "npm"]

CORPUS = [
    "git status",
    "git status && rm -rf target",
    "ls | head -5",
    "cd . ; git push || rm -r target",
    "echo $(rm -rf target)",
    "echo `rm -rf target`",
    "cat <(rm -rf target)",
    'for f in a b; do rm -rf "$f"; done',
    "if true; then rm -rf target; fi",
    "(cd . && rm -rf target)",
    "\\rm -rf target",
    'r"m" -rf target',
    "'rm' -rf target",
    '""r""m -rf target',
    "FOO=1 rm -rf target",
    "sh -c 'rm -rf target'",
    'bash -c "git status; rm -rf target"',
    "X=rm; $X -rf target",
    "eval 'rm -rf target'",
    "f(){ rm -rf target; }; f",
    "cat <<EOF\n$(rm -rf target)\nEOF",
    "echo cm0gLXJmIHRhcmdldA== | base64 -d | sh",
    "ls | xargs rm -rf",
    "grep x f;" + " " * 50 + "rm -rf target",
    "true && make build > out.txt",
    'echo "\\"; rm -rf target; echo \\""',
    "command rm -rf target",
    "exec rm -rf target",
]


@pytest.fixture
def shell_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ran.log"
    for name in STANDINS:
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho {name} >> "{log}"\n')
        stub.chmod(0o755)
    for shell in ("sh", "bash"):
        real = shutil.which(shell)
        if real:
            (bin_dir / shell).symlink_to(real)
    work = tmp_path / "work"
    work.mkdir()
    return {"PATH": str(bin_dir), "HOME": str(work)}, work, log


@pytest.mark.parametrize("text", CORPUS)
def test_what_the_shell_runs_is_what_the_parser_saw(shell_env, text):
    env, work, log = shell_env
    subprocess.run(["sh", "-c", text], cwd=work, env=env, capture_output=True, timeout=10)
    ran = set(log.read_text().split()) if log.exists() else set()

    parsed = parse_command(["sh", "-c", text])
    seen = {c.program for c in parsed.commands}

    assert parsed.opaque or ran <= seen, f"the shell ran {sorted(ran - seen)}, which the parser did not report"


@pytest.mark.parametrize(
    "text, expected",
    [("echo $(rm -rf target)", {"rm"}), ("sh -c 'git status; rm -rf target'", {"git", "rm"}),
     ("eval 'rm -rf target'", {"rm"}), ("ls | xargs rm -rf", {"ls", "xargs"})],
)
def test_the_harness_sees_what_the_shell_runs(shell_env, text, expected):
    # The comparison above is only as good as this log: it must catch programs
    # run in substitutions, nested shells and eval.
    env, work, log = shell_env
    subprocess.run(["sh", "-c", text], cwd=work, env=env, capture_output=True, timeout=10)
    assert set(log.read_text().split()) == expected
