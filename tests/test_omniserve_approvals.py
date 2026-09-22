"""Durable runs, D2d: approving and resuming runs over OmniServe.

A run that pauses for approval returns `awaiting_approval` from `/run/sync`
and in the SSE `complete` event. `GET /runs/{run_id}` shows it (never its
saved conversation), `POST /runs/{run_id}/approvals/{approval_id}` decides an
approval, and `POST /runs/{run_id}/resume` continues it.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from omnicoreagent import OmniServe, OmniServeConfig
from test_run_suspend import DELETE, WRITE_AND_DELETE, RecordingModel, _agent, _file


def _server(tmp_path, *turns):
    agent = asyncio.run(_agent(tmp_path, RecordingModel(*turns)))
    return agent, OmniServe(agent, OmniServeConfig(request_timeout=10))


def _pause(client):
    response = client.post("/run/sync", json={"query": "tidy up", "session_id": "served"})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_paused_run_is_approved_and_resumed_over_http(tmp_path):
    agent, server = _server(tmp_path, WRITE_AND_DELETE, DELETE, "cleaned up")
    with TestClient(server.app) as client:
        paused = _pause(client)
        assert paused["status"] == "awaiting_approval" and paused["response"] is None
        (approval,) = paused["approvals"]
        assert approval["tool_name"] == "delete_file"
        assert approval["arguments"] == {"path": "old.txt"}

        run = client.get(f"/runs/{paused['run_id']}")
        assert run.status_code == 200
        body = run.json()
        assert body["status"] == "awaiting_approval" and "context" not in body
        assert [a["approval_id"] for a in body["approvals"]] == [approval["approval_id"]]
        # A person deciding over HTTP sees the call as the model made it —
        # found by the steward's P3: an approver could not see which branch
        # or files a GitHub write was for.
        (shown,) = body["approvals"]
        assert shown["status"] == "pending"
        assert shown["arguments"] == {"path": "old.txt"}

        decided = client.post(
            f"/runs/{paused['run_id']}/approvals/{approval['approval_id']}",
            json={"decision": "approve", "approver": "alice"},
        )
        assert decided.status_code == 200 and decided.json()["status"] == "approved"

        resumed = client.post(f"/runs/{paused['run_id']}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "success"
        assert resumed.json()["response"] == "cleaned up"
    assert not _file(tmp_path, "old.txt").exists()


def test_approval_routes_report_errors_with_the_right_status(tmp_path):
    agent, server = _server(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    with TestClient(server.app) as client:
        paused = _pause(client)
        (approval,) = paused["approvals"]
        path = f"/runs/{paused['run_id']}/approvals/{approval['approval_id']}"

        assert client.get("/runs/run_nope").status_code == 404
        assert client.post("/runs/run_nope/resume").status_code == 404
        assert client.post(f"/runs/{paused['run_id']}/resume").status_code == 409
        assert client.post(path, json={"decision": "maybe", "approver": "a"}).status_code == 422
        assert client.post(f"/runs/{paused['run_id']}/approvals/nope", json={"decision": "approve", "approver": "a"}).status_code == 404
        assert client.post(path, json={"decision": "deny", "approver": "a", "note": "no"}).status_code == 200
        assert client.post(path, json={"decision": "approve", "approver": "b"}).status_code == 409


def test_the_sse_stream_reports_a_paused_run(tmp_path):
    agent, server = _server(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    with TestClient(server.app) as client:
        with client.stream("POST", "/run", json={"query": "tidy up", "session_id": "sse"}) as stream:
            text = "".join(chunk for chunk in stream.iter_text())

    events = [block for block in text.split("\n\n") if block.strip()]
    complete = next(b for b in events if b.startswith("event: complete"))
    payload = json.loads(complete.split("data: ", 1)[1])
    assert payload["status"] == "awaiting_approval"
    assert payload["approvals"][0]["tool_name"] == "delete_file"
    assert any(b.startswith("event: run_suspended") for b in events)


def test_a_paused_run_is_steered_over_http_and_hears_it_on_resume(tmp_path):
    agent, server = _server(tmp_path, WRITE_AND_DELETE, DELETE, "done")
    with TestClient(server.app) as client:
        paused = _pause(client)
        (approval,) = paused["approvals"]
        run = f"/runs/{paused['run_id']}"

        steered = client.post(f"{run}/steer", json={"message": "keep a backup", "sender": "alice"})
        assert steered.status_code == 200 and steered.json()["status"] == "queued"
        assert client.post(f"{run}/interrupt").status_code == 409  # only a running run
        assert client.post("/runs/run_nope/steer", json={"message": "x"}).status_code == 404
        client.post(f"{run}/approvals/{approval['approval_id']}", json={"decision": "approve", "approver": "a"})
        assert client.post(f"{run}/resume").status_code == 200

    model = agent.llm_connection
    assert "keep a backup" in json.dumps(model.calls[-1])
