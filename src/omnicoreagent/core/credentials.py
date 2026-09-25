"""The runtime's own credentials never reach the model or any record.

A command can print a credential the runtime holds without ever being given it:
a command running as the same user can read the runtime's environment from
``/proc``, ``cat`` a ``.env`` in the project, or dump its configuration. A Harbor
trial did the first while looking for a "vault" (finding 53). Passing the
environment to commands by name keeps a key out of *their* environment; it
cannot keep it out of what they can read.

So the runtime remembers the credentials it holds — the model's key, and values
of its environment variables named like credentials — and replaces them, where
they appear literally, in what a tool returns before the model sees it, and in
everything telemetry records. Process-wide: a credential one agent holds is
not something any agent in the process should hand to a model.

What this cannot do, said plainly: stop a command from *sending* a credential
somewhere without printing it. That is what an isolating sandbox, or keeping the
key out of the environment the commands run in, is for.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Mapping
from typing import Any

MARKER = "[REDACTED:credential]"

# The model's key is registered from 8 characters: an API key is never shorter,
# and a test's "k" must not redact every k in every output.
_MIN_CONFIGURED = 8
# An environment value is taken for a credential only when it looks like one.
_MIN_ENVIRONMENT = 12
# Not AUTH: GIT_AUTHOR_EMAIL is an address, and scrubbing it would corrupt every
# `git log`. An Authorization header is recognized by its own name.
_SENSITIVE_NAME = re.compile(
    r"API_?KEY|ACCESS_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_?KEY",
    re.IGNORECASE,
)

_lock = threading.Lock()
_values: frozenset[str] = frozenset()
# Longest first, so a key that contains another is replaced whole.
_ordered: tuple[str, ...] = ()


def register_credential(value: Any, *, min_length: int = _MIN_CONFIGURED) -> None:
    """Remember ``value`` as a credential, if it is long enough to be one."""
    global _values, _ordered
    if not isinstance(value, str):
        return
    value = value.strip()
    if len(value) < min_length or value in _values:
        return
    with _lock:
        _values = _values | {value}
        _ordered = tuple(sorted(_values, key=len, reverse=True))


def looks_like_credential(value: str) -> bool:
    """Whether an environment value named like a credential is one.

    ``PASSWORD_STORE_DIR`` holds a path; scrubbing it would corrupt every
    output that names the directory. A credential is a single token of some
    length that is not a path, a number or a word like ``false``.
    """
    if not isinstance(value, str):
        return False
    value = value.strip()
    if len(value) < _MIN_ENVIRONMENT or any(ch.isspace() for ch in value):
        return False
    if value.startswith(("/", "./", "../", "~")):
        return False
    if value.isdigit():
        return False
    return True


def register_environment(environ: Mapping[str, str] | None = None) -> None:
    """Remember the credentials in an environment: values of variables named
    like credentials that look like one."""
    for name, value in (os.environ if environ is None else environ).items():
        if _SENSITIVE_NAME.search(name) and looks_like_credential(value):
            register_credential(value, min_length=_MIN_ENVIRONMENT)


def register_config_credentials(config: Any) -> None:
    """Remember the credentials in a configuration: every value under a key
    named like a credential, however deep, and an ``Authorization`` header's
    token with or without its scheme."""
    if isinstance(config, dict):
        for key, value in config.items():
            name = str(key)
            is_header = name.lower() in {"authorization", "proxy-authorization"}
            if isinstance(value, str) and (is_header or _SENSITIVE_NAME.search(name)):
                register_credential(value)
                if is_header and " " in value.strip():
                    # "Bearer <token>": the token is the credential.
                    register_credential(value.strip().split(" ", 1)[1])
            else:
                register_config_credentials(value)
    elif isinstance(config, (list, tuple)):
        for item in config:
            register_config_credentials(item)


def scrub_credentials(value: Any) -> Any:
    """A copy of ``value`` with every registered credential replaced."""
    ordered = _ordered
    if not ordered:
        return value
    return _scrub(value, ordered)


def _scrub(value: Any, ordered: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for secret in ordered:
            if secret in value:
                value = value.replace(secret, MARKER)
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, ordered) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, ordered) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item, ordered) for item in value)
    return value
