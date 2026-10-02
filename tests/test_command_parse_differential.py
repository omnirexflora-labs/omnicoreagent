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

STANDINS = ["rm", "git", "ls", "cat", "head", "wc", "grep", "date", "make", "base64", "curl",
            "touch", "true", "sed", "sort", "npm", "test", "["]
# Wrappers run what follows them; by design the parser reports the wrapper
# only (simple-policy plan, decision 3). Stubbed as real pass-throughs: as
# no-op loggers they hid every program a wrapper ran (the rc7 gate, area S).
WRAPPERS = ["xargs", "find", "sudo", "timeout", "nohup", "env", "nice"]

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
    # The simple policy (SP4): forms that got past earlier parsers, and more.
    "{rm,-rf,target}",
    "r''m -rf target",
    "r\\\nm -rf target",
    "$'rm' -rf target",
    "time rm -rf target",
    "! rm -rf target",
    "rm -rf target &",
    "git status & rm -rf target",
    "coproc rm -rf target",
    "echo $((1)) && rm -rf target",
    "cat <<< $(rm -rf target)",
    "A=1 B=2 rm -rf target",
    "git log >& out.txt",
    "builtin command rm -rf target",
    "ls\nrm -rf target",
    "ls;rm -rf target",
    "ls||rm -rf target",
    "nohup rm -rf target",
    "env rm -rf target",
    "find . -exec rm -rf target ;",
    "[ -d target ] && rm -rf target",
    "test -d target && rm -rf target",
    # Builtins that run a string they are given (the rc7 gate, area S).
    "trap 'rm -rf target' EXIT; true",
    "alias ls='rm -rf target'\nls",
    "hash -p ./rm ls; ls",
    "printf -v 'a[$(rm -rf target)]' x",
    "test -v 'a[$(rm -rf target)]'",
    "let 'a[$(rm -rf target)]=1'",
    "mapfile -c 1 -C 'rm -rf target' arr < /dev/null",
    "complete -C 'rm -rf target' ls",
    "fc -e 'rm -rf target' -1",
    "unset 'a[$(rm -rf target)]'",
    'echo "a[$(rm -rf target)]"',
    "nice rm -rf target",
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
    for name in WRAPPERS:
        stub = bin_dir / name
        # Logs itself, then runs its first non-option argument onward, as
        # the real wrapper would (find: what follows -exec).
        stub.write_text(
            f'#!/bin/sh\necho {name} >> "{log}"\n'
            'while [ $# -gt 0 ]; do case "$1" in -exec) shift; break;; -*|[0-9]*|.) shift;; *) break;; esac; done\n'
            '[ $# -gt 0 ] && exec "$@"\n'
        )
        stub.chmod(0o755)
    for shell in ("sh", "bash"):
        real = shutil.which(shell)
        if real:
            (bin_dir / shell).symlink_to(real)
    work = tmp_path / "work"
    work.mkdir()
    return {"PATH": str(bin_dir), "HOME": str(work)}, work, log


@pytest.mark.parametrize("shell", ["sh", "bash"])
@pytest.mark.parametrize("text", CORPUS)
def test_what_the_shell_runs_is_what_the_parser_saw(shell_env, text, shell):
    # Under bash too: a sandbox's sh may be bash, which expands braces where
    # dash does not.
    env, work, log = shell_env
    if not shutil.which(shell):
        pytest.skip(f"no {shell} here")
    subprocess.run([shell, "-c", text], cwd=work, env=env, capture_output=True, timeout=10)
    ran = set(log.read_text().split()) if log.exists() else set()

    parsed = parse_command(["sh", "-c", text])
    seen = {c.program for c in parsed.commands}

    # A wrapper the parser reported may run anything: a rule on the wrapper's
    # name decides it (the policy reference says so).
    assert parsed.opaque or ran <= seen or seen & set(WRAPPERS), (
        f"the shell ran {sorted(ran - seen)}, which the parser did not report"
    )


@pytest.mark.parametrize(
    "text, expected",
    [("echo $(rm -rf target)", {"rm"}), ("sh -c 'git status; rm -rf target'", {"git", "rm"}),
     ("eval 'rm -rf target'", {"rm"}), ("ls | xargs rm -rf", {"ls", "xargs", "rm"})],
)
def test_the_harness_sees_what_the_shell_runs(shell_env, text, expected):
    # The comparison above is only as good as this log: it must catch programs
    # run in substitutions, nested shells and eval.
    env, work, log = shell_env
    subprocess.run(["sh", "-c", text], cwd=work, env=env, capture_output=True, timeout=10)
    assert set(log.read_text().split()) == expected
