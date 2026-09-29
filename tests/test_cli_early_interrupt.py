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
        [sys.executable, "-c", "from omnicoreagent.cli import main; main()",
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
