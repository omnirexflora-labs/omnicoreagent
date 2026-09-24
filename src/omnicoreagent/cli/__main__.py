"""``python -m omnicoreagent.cli`` — the same command as the ``omnicoreagent``
script, for a caller that cannot rely on a console script being on PATH: a
harness starting a run as a subprocess, or a checkout used through
``PYTHONPATH``.
"""

from omnicoreagent.cli import main

if __name__ == "__main__":
    main()
