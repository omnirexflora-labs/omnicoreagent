"""The support desk's scale-out override parses and says what the docs say.

The measurement (2026-10-07, server): two 1-CPU replicas behind nginx, sharing
one Postgres, peaked at 9.4 runs a second against 5.5 for one process on the
same two cores. ``apps/support_desk/scale/`` is that setup. It was run with the
admission limit lifted to find the knee; the example must not carry that
setting, so the default (24 per replica) applies.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DESK = Path(__file__).resolve().parents[1] / "apps" / "support_desk"
BASE = DESK / "compose.yml"
OVERRIDE = DESK / "scale" / "compose.replicas.yml"
NGINX = DESK / "scale" / "nginx.conf"


def _docker_compose_works() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


class _ComposeLoader(yaml.SafeLoader):
    """Plain YAML does not know Compose's ``!override`` tag; read it as data."""


_ComposeLoader.add_constructor(
    "!override", lambda loader, node: loader.construct_sequence(node)
)


def test_the_override_is_what_the_readme_describes():
    text = OVERRIDE.read_text()
    # No absolute paths from the server it ran on, and no knee-search setting.
    assert "/opt/" not in text and "/home/" not in text
    assert "MAX_CONCURRENT_RUNS" not in text
    services = yaml.load(text, Loader=_ComposeLoader)["services"]
    assert set(services) == {"desk", "desk2", "lb"}
    assert services["desk"]["cpus"] == 1
    assert services["desk2"]["cpus"] == 1
    assert services["desk2"]["extends"] == {"file": "compose.yml", "service": "desk"}
    assert services["lb"]["image"].startswith("nginx:")
    mount = services["lb"]["volumes"][0]
    assert (DESK / mount.split(":")[0]).resolve() == NGINX.resolve()


def test_nginx_balances_the_two_replicas():
    conf = NGINX.read_text()
    assert "least_conn" in conf
    assert "server desk:8000;" in conf and "server desk2:8000;" in conf
    assert "proxy_buffering off;" in conf  # the SSE stream of /run


@pytest.mark.skipif(not _docker_compose_works(), reason="docker compose not available")
def test_docker_compose_accepts_both_files():
    out = subprocess.run(
        [
            "docker", "compose", "-p", "desk-scale-check",
            "-f", str(BASE), "-f", str(OVERRIDE), "config", "--format", "json",
        ],
        capture_output=True, text=True, timeout=120, cwd=DESK,
    )
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    assert {"desk", "desk2", "lb", "postgres", "redis"} <= set(services)
    assert services["desk"]["cpus"] == 1 and services["desk2"]["cpus"] == 1
    # Each replica has its own host port; only the load balancer is shared.
    ports = [
        p["published"]
        for name in ("desk", "desk2", "lb")
        for p in services[name]["ports"]
    ]
    assert len(set(ports)) == 3
    # The replicas share the one store, and neither lifts the admission limit.
    for name in ("desk", "desk2"):
        env = services[name]["environment"]
        assert env["DATABASE_URL"].startswith("postgresql")
        assert "OMNICOREAGENT_SERVE_MAX_CONCURRENT_RUNS" not in env
