"""OmniServe's public metrics, rate limit and CORS defaults.

The rc7 security review: /prometheus (no token) named a counter after every
raw request path, so run and session ids an authenticated caller used were
readable without a token and unique paths grew the counters for good (S4-A);
the rate limit keyed on a client-supplied X-Forwarded-For, so rotating it
never tripped the limit (S4-B); CORS allowed any origin with credentials by
default, so any web page could read a server running without a token (S4-C).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig


def _agent():
    agent = MagicMock(spec=OmniCoreAgent)
    agent.name = "TestAgent"
    agent.generate_session_id.return_value = "test-session-id"
    agent.run = AsyncMock(return_value={"response": "ok"})
    return agent


def test_metrics_name_routes_not_paths():
    server = OmniServe(agent=_agent(), config=OmniServeConfig(auth_enabled=True, auth_token="t"))
    client = TestClient(server.app)
    client.get("/runs/run_TOPSECRET123", headers={"Authorization": "Bearer t"})
    for i in range(50):
        client.get(f"/nope/{i}")

    text = client.get("/prometheus").text
    assert "TOPSECRET" not in text
    assert "nope" not in text
    counters = [line for line in text.splitlines() if line.startswith("omniserve_requests_") and "_total" in line]
    assert len(counters) < 10, counters


def test_a_forwarded_header_does_not_choose_the_rate_limit_key():
    config = OmniServeConfig(rate_limit_enabled=True, rate_limit_requests=3, rate_limit_window=60)
    client = TestClient(OmniServe(agent=_agent(), config=config).app)

    codes = [
        client.get("/nope", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
        for i in range(6)
    ]
    assert 429 in codes, codes


def test_a_trusted_proxy_forwards_the_client_address():
    config = OmniServeConfig(
        rate_limit_enabled=True, rate_limit_requests=2, rate_limit_window=60,
        trusted_proxies=["testclient"],
    )
    client = TestClient(OmniServe(agent=_agent(), config=config).app)

    rotating = [client.get("/nope", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(4)]
    same = [client.get("/nope", headers={"X-Forwarded-For": "10.9.9.9"}).status_code for _ in range(3)]
    assert 429 not in rotating and same[-1] == 429


def test_open_cors_never_sends_credentials():
    client = TestClient(OmniServe(agent=_agent(), config=OmniServeConfig()).app)
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-credentials") != "true"

    explicit = OmniServeConfig(cors_origins=["https://app.example"], cors_credentials=True)
    client = TestClient(OmniServe(agent=_agent(), config=explicit).app)
    response = client.get("/health", headers={"Origin": "https://app.example"})
    assert response.headers.get("access-control-allow-credentials") == "true"
