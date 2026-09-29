"""Ctrl-C before the run starts stops cleanly: exit 6, nothing ran.

The 0.5.0rc2 gate: a Ctrl-C in the first seconds of `omnicoreagent run`
(loading the agent file, importing) hung the process 3 times in 45, because
the KeyboardInterrupt fired inside Python's import machinery and left an
import lock held; otherwise it gave click's "Aborted!" and exit 1.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time


def test_ctrl_c_while_the_agent_loads_exits_interrupted_and_runs_nothing(tmp_path):
    agent_file = tmp_path / "agent.py"
    agent_file.write_text(
        "import time\n"
        "time.sleep(3)  # a slow import\n"
        "from omnicoreagent import OmniCoreAgent\n"
        "agent = OmniCoreAgent(name='a', system_instruction='x',\n"
        "    model_config={'provider': 'openai', 'model': 'gpt-5.4-mini', 'api_key': 'k'})\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "from omnicoreagent._cli_entry import main; main()",
         "run", "--agent", str(agent_file), "-i", "hello"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(1.5)
    process.send_signal(signal.SIGINT)
    try:
        _, err = process.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("the command hung after Ctrl-C")

    assert process.returncode == 6, err
    assert "nothing ran" in err
    assert "Aborted" not in err


def test_the_handler_is_installed_before_the_cli_is_imported():
    # The 0.5.0rc3 gate: the CLI's own imports took 1-4 s on a loaded host; a
    # Ctrl-C then gave a traceback and exit 130, or was swallowed inside the
    # import machinery and the run went ahead. The console script notes it
    # before importing the CLI.
    script = (
        "import sys\n"
        "sys.argv = ['omnicoreagent', 'run']\n"
        "import omnicoreagent._early_interrupt as early\n"
        "real = early.install\n"
        "def spy():\n"
        "    print('cli imported first' if 'omnicoreagent.cli' in sys.modules else 'handler first')\n"
        "    return real()\n"
        "early.install = spy\n"
        "from omnicoreagent._cli_entry import main\n"
        "main()\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)

    assert done.stdout.splitlines()[0] == "handler first", done.stdout + done.stderr


def test_the_console_script_is_the_early_entry_point():
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["omnicoreagent"] == "omnicoreagent._cli_entry:main"
