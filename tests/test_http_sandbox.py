"""E7c: the HTTP sandbox backend (bring your own sandbox service).

A real HTTP server stands in for the service — a Cloudflare Worker, a function
on your own infrastructure — so the contract is exercised over the wire:
sessions, commands, files, termination, the bearer token, and what happens
when the service fails.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from omnicoreagent.sandbox import (
    SandboxAuthorityContext,
    SandboxExecRequest,
    SandboxManifest,
    SandboxUnsupportedError,
    build_sandbox_runtime,
)
from omnicoreagent.sandbox.models import NetworkPolicy

AUTHORITY = SandboxAuthorityContext(
    authority_request_id="authreq_test", decision_id="decision_test",
    matched_rule_ids=["allow_sandboxed_execution"], reason_code="matched_allow",
)
TOKEN = "service-token"


class Service(BaseHTTPRequestHandler):
    """A tiny sandbox service: it records what it is asked and answers."""

    seen: list = []
    files: dict = {}
    fail_with: int | None = None

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def _send(self, status, payload=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream" if raw else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        if Service.fail_with:
            return self._send(Service.fail_with, {"error": "boom"})
        path = urlparse(self.path).path
        payload = json.loads(self._body() or b"{}")
        Service.seen.append((path, payload))
        if path == "/sessions":
            return self._send(200, {"session_id": "remote-1"})
        if path.endswith("/exec"):
            command = " ".join(payload["command"])
            return self._send(
                200,
                {"exit_code": 0, "stdout": f"ran {command}", "stderr": "", "timed_out": False},
            )
        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        query = parse_qs(urlparse(self.path).query)
        Service.files[query["path"][0]] = self._body()
        Service.seen.append(("PUT", query["path"][0]))
        self._send(204)

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        self._send(200, raw=Service.files.get(query["path"][0], b""))

    def do_DELETE(self):
        Service.seen.append(("DELETE", urlparse(self.path).path))
        self._send(204)

    def log_message(self, *args):  # keep the test output clean
        pass


@pytest.fixture
def service():
    Service.seen, Service.files, Service.fail_with = [], {}, None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _runtime(base_url, **options):
    return build_sandbox_runtime(
        {"provider": "http", "options": {"base_url": base_url, "token": TOKEN, **options}}
    )


@pytest.mark.asyncio
async def test_a_session_commands_and_files_go_over_the_contract(service):
    runtime = _runtime(service)
    manifest = SandboxManifest(
        image="python:3.12-slim",
        environment={"plain": {"GREETING": "hello"}},
        network_policy=NetworkPolicy(default="deny"),
    )

    session = await runtime.create(manifest)
    result = await runtime.execute(
        session.session_id,
        SandboxExecRequest(command=["echo", "hi"], authority=AUTHORITY, timeout_seconds=5),
    )
    await runtime.write_file(session.session_id, "note.txt", b"data")
    back = await runtime.read_file(session.session_id, "note.txt")
    await runtime.terminate(session.session_id)

    create_path, create_payload = Service.seen[0]
    assert create_path == "/sessions"
    manifest_sent = create_payload["manifest"]
    assert manifest_sent["image"] == "python:3.12-slim"
    assert manifest_sent["working_dir"] == "/workspace"
    assert manifest_sent["environment"] == {"GREETING": "hello"}
    assert manifest_sent["network"]["default"] == "deny"
    exec_path, exec_payload = Service.seen[1]
    assert exec_path == "/sessions/remote-1/exec"
    assert exec_payload["command"] == ["echo", "hi"] and exec_payload["timeout_seconds"] == 5
    assert result.stdout == "ran echo hi" and result.exit_code == 0
    assert Service.files["/workspace/note.txt"] == b"data" and back == b"data"
    assert Service.seen[-1] == ("DELETE", "/sessions/remote-1")
    assert session.metadata["remote_session_id"] == "remote-1"


@pytest.mark.asyncio
async def test_secret_references_never_reach_the_service(service):
    runtime = _runtime(service)

    await runtime.create(
        SandboxManifest(
            environment={"plain": {"SAFE": "yes"}, "secret_refs": ["vault://db-password"]}
        )
    )

    _, payload = Service.seen[0]
    assert payload["manifest"]["environment"] == {"SAFE": "yes"}
    assert "db-password" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_a_failing_or_unauthorized_service_is_reported_clearly(service):
    Service.fail_with = 500
    runtime = _runtime(service)

    with pytest.raises(SandboxUnsupportedError, match="500"):
        await runtime.create(SandboxManifest())

    Service.fail_with = None
    without_token = build_sandbox_runtime(
        {"provider": "http", "options": {"base_url": service, "token": "wrong"}}
    )
    with pytest.raises(SandboxUnsupportedError, match="401"):
        await without_token.create(SandboxManifest())


@pytest.mark.asyncio
async def test_a_path_outside_the_working_directory_is_refused(service):
    runtime = _runtime(service)
    session = await runtime.create(SandboxManifest())

    with pytest.raises(PermissionError):
        await runtime.write_file(session.session_id, "../../etc/passwd", b"x")


def test_the_http_sandbox_needs_a_base_url():
    with pytest.raises(ValueError, match="base_url"):
        build_sandbox_runtime({"provider": "http"})
