# The Python version guard (omnicoreagent 0.3.10)

OmniCoreAgent 0.4 and later need Python 3.12. On Python 3.10 or 3.11, pip does
not fail: it picks the newest release it may install, which was 0.3.9, and 0.3.9
cannot build an agent. This package is published once, as a source
distribution only, with `requires-python = ">=3.10,<3.12"`. pip on those Pythons
now picks it, and its build stops with a message saying to use Python 3.12.

Its version, 0.3.10, is above 0.3.9 and below every 0.4 release, so PyPI's latest
version stays on the real release, and no later release needs to know about it.

Release it (once):

```bash
cd packaging/python-guard
OMNICOREAGENT_GUARD_RELEASE=1 uv build --sdist --out-dir dist
uv publish dist/omnicoreagent-0.3.10.tar.gz
```

Without `OMNICOREAGENT_GUARD_RELEASE=1` every build stops, which is the point: an
install is a build of this source distribution.

`tests/test_python_guard.py` holds its Python range, its version and its message.
