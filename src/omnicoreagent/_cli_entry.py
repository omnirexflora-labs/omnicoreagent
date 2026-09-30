"""The ``omnicoreagent`` console script: notes an early Ctrl-C, then runs the CLI."""

from __future__ import annotations

import sys


def main() -> None:
    if sys.argv[1:2] == ["run"]:
        from omnicoreagent._early_interrupt import install

        install()
    from omnicoreagent.cli import main as cli_main

    cli_main()
