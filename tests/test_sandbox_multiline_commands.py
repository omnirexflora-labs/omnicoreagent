"""A shell command may span lines.

Found by the repository steward's P3 kill rehearsal: its worker's
`python - <<'PY' … PY` was refused with "sandbox command argv items must not
contain control characters", and it spent steps finding a one-line way to
edit a file until it ran out of them. The `execute` tool runs `sh -c
<command>`; newlines and tabs are what shell scripts are made of. Other
control characters (NUL, escape sequences) are still refused.
"""

from __future__ import annotations

import pytest

from omnicoreagent.sandbox.execution import SandboxCommandSpec


@pytest.mark.parametrize(
    "script",
    [
        "python - <<'PY'\nprint('hi')\nPY",
        "for f in a b; do\n\techo $f\ndone",
        "echo one\r\necho two",
    ],
)
def test_a_multi_line_shell_script_is_a_command(script):
    spec = SandboxCommandSpec(command=["sh", "-c", script])

    assert spec.command[2] == script


@pytest.mark.parametrize("bad", ["echo \x00", "echo \x1b[31mred", "echo \x7f"])
def test_other_control_characters_are_still_refused(bad):
    with pytest.raises(ValueError, match="control characters"):
        SandboxCommandSpec(command=["sh", "-c", bad])


def test_the_program_name_is_still_one_line():
    with pytest.raises(ValueError, match="control characters"):
        SandboxCommandSpec(command=["sh\nrm", "-c", "true"])
