import os
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from click.testing import CliRunner
from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig
from omnicoreagent.serve.cli import cli


@pytest.fixture(autouse=True)
def isolate_omniserve_env(monkeypatch):
    """Keep local dotenv values from changing explicit test config."""
    for key in list(os.environ):
        if key.startswith(("OMNICOREAGENT_SERVE_", "OMNICOREAGENT_BACKGROUND_")):
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
        """Verify public env vars override code values."""
        with patch.dict(
            os.environ,
            {
                "OMNICOREAGENT_SERVE_PORT": "7777",
                "OMNICOREAGENT_SERVE_AUTH_ENABLED": "true",
                "OMNICOREAGENT_SERVE_LOG_LEVEL": "DEBUG",
                "OMNICOREAGENT_BACKGROUND_TASK_STORE": "sql",
            },
        ):
            # Code says port 8000, but Env says 7777
            config = OmniServeConfig(
                port=8000, auth_enabled=False, background_task_store="in_memory"
            )

            assert config.port == 7777
            assert config.auth_enabled is True
            assert config.log_level == "DEBUG"
            assert config.background_task_store == "sql"

    def test_background_task_store_url_infers_sql(self):
        config = OmniServeConfig(
            background_task_store_url="sqlite:///custom-background.db"
        )

        assert config.background_task_store_config() == {
            "backend": "sql",
            "url": "sqlite:///custom-background.db",
            "prefix": None,
            "connect_timeout": None,
        }

    def test_background_task_store_uri_selects_mongodb(self):
        config = OmniServeConfig(
            background_task_store_uri="mongodb://localhost:27017",
            background_task_store_database="tasks",
        )

        assert config.background_task_store_config() == {
            "backend": "mongodb",
            "uri": "mongodb://localhost:27017",
            "database": "tasks",
            "collection_prefix": None,
            "connect_timeout": None,
        }

    def test_background_task_store_redis_keeps_tuning_without_url(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/3"}):
            config = OmniServeConfig(
                background_task_store="redis",
                background_task_store_prefix="tasks",
                background_task_store_connect_timeout=1.25,
            )
            assert config.background_task_store_config() == {
                "backend": "redis",
                "url": "redis://localhost:6379/3",
                "prefix": "tasks",
                "connect_timeout": 1.25,
            }

    def test_background_task_store_mongodb_keeps_tuning_without_uri(self):
        with patch.dict(os.environ, {"MONGODB_URI": "mongodb://localhost:27017"}):
            config = OmniServeConfig(
                background_task_store="mongodb",
                background_task_store_database="tasks",
                background_task_store_collection_prefix="background_tasks",
                background_task_store_connect_timeout=2.5,
            )
            assert config.background_task_store_config() == {
                "backend": "mongodb",
                "uri": "mongodb://localhost:27017",
                "database": "tasks",
                "prefix": None,
                "collection_prefix": "background_tasks",
                "connect_timeout": 2.5,
            }

    def test_env_example_includes_background_settings(self):
        result = CliRunner().invoke(cli, ["config", "--env-example"])

        assert result.exit_code == 0
        for key in [
            "OMNICOREAGENT_BACKGROUND_TASK_STORE",
            "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL",
            "OMNICOREAGENT_BACKGROUND_TASK_STORE_URI",
            "OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE",
        ]:
            assert key in result.output
        assert "export LLM_API_KEY=your_api_key_here" in result.output


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
        resp = client.post(
            "/run/sync",
            json={"query": "test"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

        # 3. Valid token -> 200
        # Mock run method for success
        mock_agent.run = AsyncMock(return_value={"response": "test"})
        resp = client.post(
            "/run/sync",
            json={"query": "test"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200

    def test_auth_respects_api_prefix_and_public_paths(self, mock_agent):
        config = OmniServeConfig(
            api_prefix="/api",
            auth_enabled=True,
            auth_token="secret123",
        )
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        assert client.get("/api/health").status_code == 200
        assert client.get("/prometheus").status_code == 200
        assert client.post("/api/run/sync", json={"query": "test"}).status_code == 401

        mock_agent.run = AsyncMock(return_value={"response": "test"})
        resp = client.post(
            "/api/run/sync",
            json={"query": "test"},
            headers={"Authorization": "Bearer secret123"},
        )
        assert resp.status_code == 200

    def test_cors_middleware(self, mock_agent):
        config = OmniServeConfig(
            cors_enabled=True, cors_origins=["https://example.com"]
        )
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        # Preflight request
        resp = client.options(
            "/run/sync",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
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

    def test_run_normalizes_string_agent_result(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "StringAgent"
        agent.generate_session_id.return_value = "string-session"
        agent.run = AsyncMock(return_value="plain response")

        server = OmniServe(agent=agent, config=OmniServeConfig())
        client = TestClient(server.app)

        resp = client.post("/run/sync", json={"query": "Hello"})

        assert resp.status_code == 200
        assert resp.json() == {
            "response": "plain response",
            "session_id": "string-session",
            "agent_name": "StringAgent",
            "metric": None,
        }

    def test_request_timeout_is_enforced_for_sync_run(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "TimeoutAgent"
        agent.generate_session_id.return_value = "timeout-session"

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(2)
            return {"response": "too late"}

        agent.run = AsyncMock(side_effect=slow_run)

        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(request_timeout=1),
        )
        client = TestClient(server.app)

        resp = client.post("/run/sync", json={"query": "slow"})

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"]

    def test_request_metrics_are_per_app_instance(self):
        agent_one = MagicMock(spec=OmniCoreAgent)
        agent_one.name = "One"
        agent_one.generate_session_id.return_value = "one-session"
        agent_one.run = AsyncMock(return_value={"response": "one"})

        agent_two = MagicMock(spec=OmniCoreAgent)
        agent_two.name = "Two"
        agent_two.generate_session_id.return_value = "two-session"
        agent_two.run = AsyncMock(return_value={"response": "two"})

        client_one = TestClient(OmniServe(agent_one, OmniServeConfig()).app)
        client_two = TestClient(OmniServe(agent_two, OmniServeConfig()).app)

        assert client_one.post("/run/sync", json={"query": "hello"}).status_code == 200

        metrics_one = client_one.get("/prometheus").text
        metrics_two = client_two.get("/prometheus").text

        assert "omniserve_requests_total 1" in metrics_one
        assert "omniserve_requests_total 0" in metrics_two
