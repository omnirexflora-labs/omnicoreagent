
import os
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig
from omnicoreagent.serve.resilience import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerOpenError


@pytest.fixture(autouse=True)
def isolate_omniserve_env(monkeypatch):
    """Keep local dotenv values from changing explicit test config."""
    for key in list(os.environ):
        if key.startswith("OMNISERVE_"):
            monkeypatch.delenv(key, raising=False)


# =============================================================================
# Test Configuration
# =============================================================================
class TestConfiguration:
    def test_default_config(self):
        config = OmniServeConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert config.workers == 1
        assert config.api_prefix == ""
        
    def test_code_overrides_defaults(self):
        config = OmniServeConfig(port=9090, host="127.0.0.1")
        assert config.port == 9090
        assert config.host == "127.0.0.1"

    def test_env_vars_override_code(self):
        """Verify OMNISERVE_* env vars override code values."""
        with patch.dict(os.environ, {
            "OMNISERVE_PORT": "7777",
            "OMNISERVE_AUTH_ENABLED": "true",
            "OMNISERVE_LOG_LEVEL": "DEBUG"
        }):
            # Code says port 8000, but Env says 7777
            config = OmniServeConfig(port=8000, auth_enabled=False)
            
            assert config.port == 7777
            assert config.auth_enabled is True
            assert config.log_level == "DEBUG"

# =============================================================================
# Test Middleware & Security
# =============================================================================
class TestMiddleware:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "TestAgent"
        agent.generate_session_id.return_value = "test-session-id"
        return agent

    def test_auth_middleware(self, mock_agent):
        config = OmniServeConfig(auth_enabled=True, auth_token="secret123")
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        # 1. No token -> 401
        resp = client.post("/run/sync", json={"query": "test"})
        assert resp.status_code == 401
        
        # 2. Invalid token -> 401
        resp = client.post("/run/sync", json={"query": "test"}, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

        # 3. Valid token -> 200
        # Mock run method for success
        mock_agent.run = AsyncMock(return_value={"response": "test"})
        resp = client.post("/run/sync", json={"query": "test"}, headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200

    def test_cors_middleware(self, mock_agent):
        config = OmniServeConfig(
            cors_enabled=True, 
            cors_origins=["https://example.com"]
        )
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        # Preflight request
        resp = client.options(
            "/run/sync",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST"
            }
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "https://example.com"

# =============================================================================
# Test Endpoints
# =============================================================================
class TestEndpoints:
    @pytest.fixture
    def server_client(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "EndpointTestAgent"
        agent.generate_session_id.return_value = "test-endpoint-session"
        # Mock run method - MUST return a dict, not a string
        agent.run = AsyncMock(return_value={"response": "Agent response"})
        # Mock get_metrics
        agent.get_metrics = AsyncMock(return_value={"total_tokens": 100})
        agent.get_trace = AsyncMock(
            return_value={
                "session_id": "test-endpoint-session",
                "summary": {"total_events": 2, "tool_calls": 1},
                "steps": [{"index": 1, "event_type": "user_message"}],
            }
        )
        
        server = OmniServe(agent=agent, config=OmniServeConfig())
        return TestClient(server.app)

    def test_health_check(self, server_client):
        resp = server_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
        assert resp.json()["agent_name"] == "EndpointTestAgent"

    def test_sync_run(self, server_client):
        resp = server_client.post("/run/sync", json={"query": "Hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Agent response"
        assert data["agent_name"] == "EndpointTestAgent"

    def test_metrics_endpoint(self, server_client):
        resp = server_client.get("/metrics")
        assert resp.status_code == 200
        # Check field directly, not nested
        assert resp.json()["total_tokens"] == 100

    def test_prometheus_endpoint(self, server_client):
        resp = server_client.get("/prometheus")
        assert resp.status_code == 200
        assert "omniserve_requests_total" in resp.text

    def test_trace_endpoint(self, server_client):
        resp = server_client.get("/events/test-endpoint-session/trace")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-endpoint-session"
        assert data["summary"]["tool_calls"] == 1
        assert data["steps"][0]["event_type"] == "user_message"

# =============================================================================
# Test Resilience
# =============================================================================
class TestResilience:
    @pytest.mark.asyncio
    async def test_circuit_breaker_transitions(self):
        config = CircuitBreakerConfig(
            failure_threshold=2, 
            success_threshold=1, 
            timeout=0.1
        )
        breaker = CircuitBreaker("test-breaker", config)

        # 1. Closed state (Initial)
        assert breaker.state.value == "closed"

        # 2. Failure 1
        breaker._record_failure(Exception("fail"))
        assert breaker.state.value == "closed"

        # 3. Failure 2 -> Opens circuit
        breaker._record_failure(Exception("fail"))
        assert breaker.state.value == "open"

        # 4. Call while OPEN -> CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            async with breaker:
                pass

        # 5. Wait for timeout -> HALF_OPEN (simulated by time passing)
        await asyncio.sleep(0.15)
        # Next call attempts to transition to HALF_OPEN
        
        # We simulate a successful call
        async with breaker:
            # Inside this block, state should ideally be checked or transition happens on entry
            pass
            
        # After success, should be CLOSED again
        assert breaker.state.value == "closed"
