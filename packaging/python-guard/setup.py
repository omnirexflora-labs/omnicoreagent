"""Stop the build and say why: OmniCoreAgent needs Python 3.12 or later.

Only the release itself (building the source distribution, with
OMNICOREAGENT_GUARD_RELEASE=1) gets past this; every install stops here.
"""

import os
import sys

MESSAGE = f"""

OmniCoreAgent needs Python 3.12 or later; this is Python {sys.version_info.major}.{sys.version_info.minor}.

Install Python 3.12 and a virtual environment for it, then install again:

    uv python install 3.12
    uv venv --python 3.12 && source .venv/bin/activate
    uv pip install omnicoreagent

https://docs-omnicoreagent.omnirexfloralabs.com/docs/getting-started/installation
"""

if os.environ.get("OMNICOREAGENT_GUARD_RELEASE") != "1":
    sys.stderr.write(MESSAGE)
    raise SystemExit(1)

from setuptools import setup  # noqa: E402

setup(py_modules=[])
