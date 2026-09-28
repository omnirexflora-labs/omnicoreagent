"""A timed-out /run/sync names its run, and the record says timeout.

Found writing the OmniServe page (D7): the general request-timeout
middleware and the route's own deadline were equal, and the middleware won:
its 504 carried no run_id, so the caller could not look the run up, and its
cancellation left the record `cancelled` while the trace said `timeout`. The
run routes answer their own timeout, like the background run route.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from omnicoreagent import OmniCoreAgent
from omnicoreagent.serve import OmniServe, OmniServeConfig


def test_a_sync_run_that_times_out_names_its_run():
    agent = MagicMock(spec=OmniCoreAgent)
    agent.name = "SlowAgent"
    agent.generate_session_id.return_value = "slow-session"
    seen: dict = {}

    async def slow_run(*args, **kwargs):
        seen["run_id"] = kwargs.get("run_id")
        await asyncio.sleep(3)
        return {"response": "too late"}

    agent.run = AsyncMock(side_effect=slow_run)
    server = OmniServe(agent=agent, config=OmniServeConfig(request_timeout=1))

    resp = TestClient(server.app).post("/run/sync", json={"query": "slow"})

    assert resp.status_code == 504
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), body
    assert detail["run_id"] == seen["run_id"]
    assert "timed out" in detail["message"]


def test_the_run_record_says_timeout(tmp_path, monkeypatch):
    import asyncio as _asyncio

    from test_telemetry_tool_record import ScriptedModel

    class SlowModel(ScriptedModel):
        async def llm_call(self, messages, tools=None, **kwargs):
            await _asyncio.sleep(3)
            return await super().llm_call(messages, tools, **kwargs)

    monkeypatch.setenv("OMNICOREAGENT_WORKSPACE_DIR", str(tmp_path))
    agent = OmniCoreAgent(
        name="slow",
        system_instruction="x",
        model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"},
        agent_config={"guardrail_mode": "off"},
    )
    client = TestClient(OmniServe(agent=agent, config=OmniServeConfig(request_timeout=1)).app)
    _asyncio.run(agent.initialize())
    agent.llm_connection = SlowModel()

    run_id = client.post("/run/sync", json={"query": "hi"}).json()["detail"]["run_id"]
    record = client.get(f"/runs/{run_id}").json()

    assert record["status"] == "timeout", record
