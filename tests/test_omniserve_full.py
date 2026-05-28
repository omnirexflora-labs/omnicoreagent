import os
import pytest
import asyncio
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from click.testing import CliRunner
from pydantic import ValidationError
from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig
from omnicoreagent.core.telemetry import (
    ActorType,
    InMemoryTelemetryStore,
    TelemetryActor,
    TelemetryEvent,
    TelemetryRecorder,
    TelemetryStream,
    TelemetryStreamScope,
)
from omnicoreagent.serve.cli import cli
from omnicoreagent.serve.app_factory import _build_background_manager
from omnicoreagent.governance import policy_from_mapping


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

    def test_api_prefix_is_normalized(self):
        assert OmniServeConfig(api_prefix="api").api_prefix == "/api"
        assert OmniServeConfig(api_prefix="/api/").api_prefix == "/api"
        assert OmniServeConfig(api_prefix="/").api_prefix == ""

    def test_env_api_prefix_is_normalized(self):
        with patch.dict(os.environ, {"OMNICOREAGENT_SERVE_API_PREFIX": "api/v1/"}):
            assert OmniServeConfig().api_prefix == "/api/v1"

    def test_log_level_is_normalized(self):
        assert OmniServeConfig(log_level="debug").log_level == "DEBUG"

    def test_env_vars_override_code(self):
        """Verify public env vars override code values."""
        with patch.dict(
            os.environ,
            {
                "OMNICOREAGENT_SERVE_PORT": "7777",
                "OMNICOREAGENT_SERVE_AUTH_ENABLED": "true",
                "OMNICOREAGENT_SERVE_AUTH_TOKEN": "env-secret",
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
            assert config.auth_token == "env-secret"
            assert config.log_level == "DEBUG"
            assert config.background_task_store == "sql"

    @pytest.mark.parametrize(
        ("env_name", "env_value", "message"),
        [
            ("OMNICOREAGENT_SERVE_PORT", "not-a-port", "must be an integer"),
            ("OMNICOREAGENT_SERVE_AUTH_ENABLED", "maybe", "must be a boolean"),
            ("OMNICOREAGENT_SERVE_LOG_LEVEL", "LOUD", "LOG_LEVEL must be one of"),
            (
                "OMNICOREAGENT_SERVE_API_PREFIX",
                "bad prefix",
                "API_PREFIX must not contain whitespace",
            ),
            (
                "OMNICOREAGENT_BACKGROUND_TASK_STORE_CONNECT_TIMEOUT",
                "slow",
                "must be a number",
            ),
        ],
    )
    def test_invalid_env_values_fail_clearly(self, env_name, env_value, message):
        with patch.dict(os.environ, {env_name: env_value}):
            with pytest.raises(ValidationError, match=message):
                OmniServeConfig()

    def test_auth_enabled_requires_non_empty_token(self):
        with pytest.raises(ValidationError, match="AUTH_TOKEN is required"):
            OmniServeConfig(auth_enabled=True)

        with pytest.raises(ValidationError, match="AUTH_TOKEN is required"):
            OmniServeConfig(auth_enabled=True, auth_token="   ")

    def test_auth_env_enabled_requires_token(self):
        with patch.dict(os.environ, {"OMNICOREAGENT_SERVE_AUTH_ENABLED": "true"}):
            with pytest.raises(ValidationError, match="AUTH_TOKEN is required"):
                OmniServeConfig()

    def test_background_manager_inherits_agent_governance_config(self):
        policy = policy_from_mapping(
            {
                "name": "serve-background-policy",
                "mode": "strict",
                "rules": {
                    "allow": [
                        {
                            "rule_id": "allow_background",
                            "capability": "background.*",
                        }
                    ]
                },
            }
        )
        agent = SimpleNamespace(
            agent=None,
            agent_config={"governance_config": {"enabled": True, "policy": policy}},
            telemetry_store=None,
            telemetry_recorder=None,
            _ensure_telemetry=lambda: None,
        )

        manager = _build_background_manager(
            agent=agent,
            config=OmniServeConfig(background_enabled=True),
            background_manager=None,
        )

        assert manager.governance_engine is not None
        assert manager.governance_engine.policy.name == "serve-background-policy"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"port": 0}, "PORT must be between 1 and 65535"),
            ({"port": 65536}, "PORT must be between 1 and 65535"),
            ({"host": ""}, "HOST must not be empty"),
            ({"workers": 0}, "WORKERS must be 1 for direct OmniServe"),
            ({"workers": 2}, "WORKERS must be 1 for direct OmniServe"),
            ({"api_prefix": "bad prefix"}, "API_PREFIX must not contain whitespace"),
            ({"log_level": "LOUD"}, "LOG_LEVEL must be one of"),
            (
                {"rate_limit_enabled": True, "rate_limit_requests": 0},
                "RATE_LIMIT_REQUESTS must be at least 1",
            ),
            (
                {"rate_limit_enabled": True, "rate_limit_window": 0},
                "RATE_LIMIT_WINDOW must be at least 1",
            ),
        ],
    )
    def test_invalid_serving_bounds_fail_clearly(self, kwargs, message):
        with pytest.raises(ValidationError, match=message):
            OmniServeConfig(**kwargs)

    @pytest.mark.parametrize(
        ("env_name", "env_value", "message"),
        [
            ("OMNICOREAGENT_SERVE_PORT", "0", "PORT must be between 1 and 65535"),
            (
                "OMNICOREAGENT_SERVE_WORKERS",
                "0",
                "WORKERS must be 1 for direct OmniServe",
            ),
            (
                "OMNICOREAGENT_SERVE_WORKERS",
                "2",
                "WORKERS must be 1 for direct OmniServe",
            ),
            (
                "OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS",
                "0",
                "RATE_LIMIT_REQUESTS must be at least 1",
            ),
            (
                "OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW",
                "0",
                "RATE_LIMIT_WINDOW must be at least 1",
            ),
        ],
    )
    def test_invalid_serving_bounds_from_env_fail_clearly(
        self, env_name, env_value, message
    ):
        env = {env_name: env_value}
        if "RATE_LIMIT" in env_name:
            env["OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED"] = "true"
        with patch.dict(os.environ, env):
            with pytest.raises(ValidationError, match=message):
                OmniServeConfig()

    def test_non_positive_request_timeout_remains_allowed_to_disable_timeout(self):
        assert OmniServeConfig(request_timeout=0).request_timeout == 0
        assert OmniServeConfig(request_timeout=-1).request_timeout == -1

    def test_server_start_overrides_are_validated_before_uvicorn(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "StartValidationAgent"
        server = OmniServe(agent, OmniServeConfig(background_enabled=False))

        with patch("uvicorn.run") as run:
            with pytest.raises(ValueError, match="PORT must be between 1 and 65535"):
                server.start(port=0)
            run.assert_not_called()

        with patch("uvicorn.run") as run:
            with pytest.raises(ValueError, match="WORKERS must be 1"):
                server.start(workers=0)
            run.assert_not_called()

        with patch("uvicorn.run") as run:
            with pytest.raises(ValueError, match="WORKERS must be 1"):
                server.start(workers=2)
            run.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_start_async_overrides_are_validated_before_uvicorn(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "AsyncStartValidationAgent"
        server = OmniServe(agent, OmniServeConfig(background_enabled=False))

        with patch("uvicorn.Server") as uvicorn_server:
            with pytest.raises(ValueError, match="PORT must be between 1 and 65535"):
                await server.start_async(port=0)
            uvicorn_server.assert_not_called()

    def test_cli_run_invalid_rate_limit_fails_before_serving(self, tmp_path):
        agent_file = tmp_path / "agent.py"
        marker_file = tmp_path / "loaded.txt"
        agent_file.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker_file)!r}).write_text('loaded', encoding='utf-8')\n"
            "class Agent:\n"
            "    name = 'CliValidationAgent'\n"
            "    agent_config = {}\n\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            cli,
            ["run", "--agent", str(agent_file), "--rate-limit", "0"],
        )

        assert result.exit_code != 0
        assert "RATE_LIMIT_REQUESTS must be at least 1" in result.output
        assert not marker_file.exists()

    def test_cli_run_invalid_port_fails_before_serving(self, tmp_path):
        agent_file = tmp_path / "agent.py"
        marker_file = tmp_path / "loaded.txt"
        agent_file.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker_file)!r}).write_text('loaded', encoding='utf-8')\n"
            "class Agent:\n"
            "    name = 'CliPortValidationAgent'\n"
            "    agent_config = {}\n\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            cli,
            ["run", "--agent", str(agent_file), "--port", "0"],
        )

        assert result.exit_code != 0
        assert "PORT must be between 1 and 65535" in result.output
        assert not marker_file.exists()

    def test_cli_run_invalid_env_port_fails_before_loading_agent(self, tmp_path):
        agent_file = tmp_path / "agent.py"
        marker_file = tmp_path / "loaded.txt"
        agent_file.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker_file)!r}).write_text('loaded', encoding='utf-8')\n"
            "class Agent:\n"
            "    name = 'CliEnvPortValidationAgent'\n"
            "    agent_config = {}\n\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            cli,
            ["run", "--agent", str(agent_file)],
            env={"OMNICOREAGENT_SERVE_PORT": "0"},
        )

        assert result.exit_code != 0
        assert "PORT must be between 1 and 65535" in result.output
        assert not marker_file.exists()

    def test_cli_run_invalid_workers_fails_before_loading_agent(self, tmp_path):
        agent_file = tmp_path / "agent.py"
        marker_file = tmp_path / "loaded.txt"
        agent_file.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker_file)!r}).write_text('loaded', encoding='utf-8')\n"
            "class Agent:\n"
            "    name = 'CliWorkerValidationAgent'\n"
            "    agent_config = {}\n\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            cli,
            ["run", "--agent", str(agent_file), "--workers", "2"],
        )

        assert result.exit_code != 0
        assert "WORKERS must be 1 for direct OmniServe" in result.output
        assert not marker_file.exists()

    def test_cli_quickstart_invalid_port_fails_before_creating_agent(self):
        with patch("omnicoreagent.OmniCoreAgent") as agent_cls:
            result = CliRunner().invoke(cli, ["quickstart", "--port", "0"])

        assert result.exit_code != 0
        assert "PORT must be between 1 and 65535" in result.output
        agent_cls.assert_not_called()

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

    def test_background_task_store_redis_requires_prefixed_url(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379/3"}):
            config = OmniServeConfig(
                background_task_store="redis",
                background_task_store_prefix="tasks",
                background_task_store_connect_timeout=1.25,
            )
            with pytest.raises(ValueError, match="TASK_STORE_URL"):
                config.background_task_store_config()

    def test_background_task_store_redis_uses_prefixed_url(self):
        config = OmniServeConfig(
            background_task_store="redis",
            background_task_store_url="redis://localhost:6379/3",
            background_task_store_prefix="tasks",
            background_task_store_connect_timeout=1.25,
        )

        assert config.background_task_store_config() == {
            "backend": "redis",
            "url": "redis://localhost:6379/3",
            "prefix": "tasks",
            "connect_timeout": 1.25,
        }

    def test_background_task_store_mongodb_requires_prefixed_uri(self):
        with patch.dict(os.environ, {"MONGODB_URI": "mongodb://localhost:27017"}):
            config = OmniServeConfig(
                background_task_store="mongodb",
                background_task_store_database="tasks",
                background_task_store_collection_prefix="background_tasks",
                background_task_store_connect_timeout=2.5,
            )
            with pytest.raises(ValueError, match="TASK_STORE_URI"):
                config.background_task_store_config()

    def test_background_task_store_mongodb_uses_prefixed_uri(self):
        config = OmniServeConfig(
            background_task_store="mongodb",
            background_task_store_uri="mongodb://localhost:27017",
            background_task_store_database="tasks",
            background_task_store_collection_prefix="background_tasks",
            background_task_store_connect_timeout=2.5,
        )

        assert config.background_task_store_config() == {
            "backend": "mongodb",
            "uri": "mongodb://localhost:27017",
            "database": "tasks",
            "collection_prefix": "background_tasks",
            "connect_timeout": 2.5,
        }

    def test_background_task_store_rejects_mismatched_url_backend(self):
        config = OmniServeConfig(
            background_task_store="mongodb",
            background_task_store_url="sqlite:///wrong.db",
        )

        with pytest.raises(ValueError, match="TASK_STORE_URL"):
            config.background_task_store_config()

    def test_background_task_store_rejects_mismatched_uri_backend(self):
        config = OmniServeConfig(
            background_task_store="redis",
            background_task_store_uri="mongodb://localhost:27017",
        )

        with pytest.raises(ValueError, match="TASK_STORE_URI"):
            config.background_task_store_config()

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
        assert "Choose one backend" in result.output
        assert "REDIS_URL" not in result.output
        assert "MONGODB_URI" not in result.output

    def test_cli_version_uses_omnicoreagent_package_version(self):
        result = CliRunner().invoke(cli, ["--version"])

        assert result.exit_code == 0
        assert result.output.strip() == f"omniserve, version {version('omnicoreagent')}"

    def test_generate_dockerfile_uses_current_image_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        agent_file = agent_dir / "agent file.py"
        marker_file = tmp_path / "loaded.txt"
        agent_file.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker_file)!r}).write_text('loaded', encoding='utf-8')\n"
            "class Agent:\n"
            "    name = 'GeneratedAgent'\n"
            "    agent_config = {}\n\n"
            "agent = Agent()\n",
            encoding="utf-8",
        )
        output_dir = tmp_path / "docker"

        result = CliRunner().invoke(
            cli,
            [
                "generate-dockerfile",
                "--file",
                str(agent_file),
                "--output-dir",
                str(output_dir),
            ],
        )

        assert result.exit_code == 0
        assert "omnicoreagent-serve" in result.output
        assert "omniserver" not in result.output
        assert "Agent path inside image: /app/agents/agent file.py" in result.output
        assert "docker build -f docker/Dockerfile -t omnicoreagent-serve ." in result.output
        assert (output_dir / "Dockerfile").exists()
        assert not marker_file.exists()

        dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")
        assert 'AGENT_PATH="/app/agents/agent file.py"' in dockerfile
        assert 'omniserve run --agent \\"$AGENT_PATH\\"' in dockerfile
        assert "HEALTHCHECK" in dockerfile
        assert "OMNICOREAGENT_SERVE_PORT:-8000" in dockerfile
        assert "OMNICOREAGENT_SERVE_API_PREFIX" in dockerfile
        assert "${prefix}/health" in dockerfile
        assert "USER appuser" in dockerfile
        assert "OMNICOREAGENT_WORKSPACE_BACKEND=local" in dockerfile
        assert "OMNICOREAGENT_WORKSPACE_DIR=/tmp/workspace" in dockerfile
        assert "LLM_API_KEY" not in dockerfile

    def test_generate_dockerfile_rejects_agent_outside_build_context(
        self, tmp_path, monkeypatch
    ):
        project_root = tmp_path / "project"
        project_root.mkdir()
        external_agent = tmp_path / "agent.py"
        external_agent.write_text("agent = object()\n", encoding="utf-8")
        monkeypatch.chdir(project_root)

        result = CliRunner().invoke(
            cli,
            [
                "generate-dockerfile",
                "--file",
                str(external_agent),
                "--output-dir",
                str(project_root / "docker"),
            ],
        )

        assert result.exit_code != 0
        assert "Agent file must be inside the current Docker build context" in result.output
        assert not (project_root / "docker" / "Dockerfile").exists()


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

    def test_auth_protects_telemetry_and_background_routes_with_api_prefix(self):
        class LightweightAgent:
            name = "BoundaryAgent"

            def generate_session_id(self):
                return "boundary-session"

            def get_telemetry_events_after(self, **kwargs):
                return []

        config = OmniServeConfig(
            api_prefix="/api",
            auth_enabled=True,
            auth_token="secret123",
            background_start_worker=False,
        )
        server = OmniServe(agent=LightweightAgent(), config=config)
        client = TestClient(server.app)

        assert client.get("/api/ready").status_code == 200
        assert client.get("/api/telemetry/events").status_code == 401
        assert client.get("/api/background/status").status_code == 401

        telemetry = client.get(
            "/api/telemetry/events",
            headers={"Authorization": "Bearer secret123"},
        )
        assert telemetry.status_code == 200
        assert telemetry.json()["events"] == []

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

    def test_rate_limit_middleware_allows_then_denies_protected_routes(self, mock_agent):
        config = OmniServeConfig(
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window=60,
        )
        mock_agent.run = AsyncMock(return_value={"response": "test"})
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        public_response = client.get("/health")
        assert public_response.status_code == 200
        assert "X-RateLimit-Limit" not in public_response.headers

        allowed = client.post("/run/sync", json={"query": "first"})
        denied = client.post("/run/sync", json={"query": "second"})

        assert allowed.status_code == 200
        assert allowed.headers["X-RateLimit-Limit"] == "1"
        assert allowed.headers["X-RateLimit-Remaining"] == "0"
        assert denied.status_code == 429
        assert denied.headers["Retry-After"]
        assert denied.json()["error"] == "TooManyRequests"

    def test_rate_limit_counts_unauthenticated_protected_requests(self, mock_agent):
        config = OmniServeConfig(
            auth_enabled=True,
            auth_token="secret123",
            rate_limit_enabled=True,
            rate_limit_requests=2,
            rate_limit_window=60,
        )
        server = OmniServe(agent=mock_agent, config=config)
        client = TestClient(server.app)

        first = client.post("/run/sync", json={"query": "first"})
        second = client.post("/run/sync", json={"query": "second"})
        third = client.post("/run/sync", json={"query": "third"})

        assert first.status_code == 401
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"
        assert second.status_code == 401
        assert second.headers["X-RateLimit-Remaining"] == "0"
        assert third.status_code == 429
        assert third.json()["error"] == "TooManyRequests"

    def test_rate_limit_applies_to_telemetry_and_exempts_readiness(self):
        class LightweightAgent:
            name = "RateLimitedTelemetryAgent"

            def generate_session_id(self):
                return "rate-limit-session"

            def get_telemetry_events_after(self, **kwargs):
                return []

        config = OmniServeConfig(
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window=60,
            background_enabled=False,
        )
        server = OmniServe(agent=LightweightAgent(), config=config)
        client = TestClient(server.app)

        readiness = client.get("/ready")
        assert readiness.status_code == 200
        assert "X-RateLimit-Limit" not in readiness.headers

        allowed = client.get("/telemetry/events")
        denied = client.get("/telemetry/events")

        assert allowed.status_code == 200
        assert allowed.headers["X-RateLimit-Limit"] == "1"
        assert denied.status_code == 429
        assert denied.json()["error"] == "TooManyRequests"


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
        agent.get_latest_trace = AsyncMock(
            return_value={
                "trace_id": "trace_endpoint",
                "run_id": "run_endpoint",
                "session_id": "test-endpoint-session",
                "status": "completed",
                "events": [{"event_type": "user_message"}],
                "spans": [{"kind": "agent.run"}],
            }
        )
        agent.get_trace = AsyncMock(
            side_effect=lambda **kwargs: {
                "trace_id": kwargs.get("trace_id") or "trace_endpoint_by_run",
                "run_id": kwargs.get("run_id") or "run_endpoint",
                "session_id": "test-endpoint-session",
                "status": "completed",
                "events": [{"event_type": "final_answer"}],
                "spans": [{"kind": "agent.run"}],
            }
        )
        agent.list_telemetry_traces = AsyncMock(
            return_value=[
                {
                    "trace_id": "trace_endpoint",
                    "run_id": "run_endpoint",
                    "session_id": "test-endpoint-session",
                    "status": "completed",
                    "events": [{"event_type": "user_message"}],
                    "spans": [{"kind": "agent.run"}],
                }
            ]
        )
        store = InMemoryTelemetryStore()
        agent.telemetry_store = store
        agent.get_telemetry_events_after = AsyncMock(
            return_value=[
                TelemetryEvent(
                    trace_id="trace_endpoint",
                    span_id="span_endpoint",
                    event_type="user_message",
                    actor=TelemetryActor(type=ActorType.USER),
                    metadata={
                        "session_id": "test-endpoint-session",
                        "run_id": "run_endpoint",
                    },
                )
            ]
        )

        server = OmniServe(agent=agent, config=OmniServeConfig())
        return TestClient(server.app)

    def test_health_check(self, server_client):
        resp = server_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
        assert resp.json()["agent_name"] == "EndpointTestAgent"
        assert resp.json()["version"] == version("omnicoreagent")

    def test_readiness_requires_lifespan_startup(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "ReadinessAgent"
        agent.generate_session_id.return_value = "readiness-session"

        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app)

        resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json() == {
            "ready": False,
            "agent_name": "ReadinessAgent",
            "initialized": True,
            "mcp_connected": True,
        }

    def test_readiness_true_after_successful_lifespan_startup(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "ReadyAgent"
        agent.generate_session_id.return_value = "ready-session"

        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json() == {
            "ready": True,
            "agent_name": "ReadyAgent",
            "initialized": True,
            "mcp_connected": True,
        }

    def test_readiness_does_not_require_mcp_when_no_servers_are_configured(self):
        class LocalOnlyAgent:
            name = "LocalOnlyAgent"
            mcp_tools = []
            mcp_client = None
            _initialized = True

            def generate_session_id(self):
                return "local-only-session"

            async def connect_mcp_servers(self):
                return None

        server = OmniServe(
            agent=LocalOnlyAgent(),
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json() == {
            "ready": True,
            "agent_name": "LocalOnlyAgent",
            "initialized": True,
            "mcp_connected": True,
        }

    def test_readiness_reflects_uninitialized_agent(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "UninitializedAgent"
        agent.generate_session_id.return_value = "uninitialized-session"
        agent._initialized = False

        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is False
        assert resp.json()["initialized"] is False

    def test_readiness_reflects_mcp_connection_state(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "MCPReadinessAgent"
        agent.generate_session_id.return_value = "mcp-readiness-session"
        agent.mcp_tools = [{"name": "remote_tools", "transport_type": "stdio"}]
        agent.mcp_client = None

        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is False
        assert resp.json()["mcp_connected"] is False

    @pytest.mark.parametrize(
        ("sessions", "expected_ready"),
        [
            ({"server_one": {"connected": True}, "server_two": {"connected": True}}, True),
            (
                {
                    "server_one": {"connected": True},
                    "server_two": {"connected": False},
                },
                False,
            ),
            ({"server_one": {"connected": True}}, False),
            (
                {
                    "server_one": {"connected": True},
                    "unconfigured_server": {"connected": True},
                },
                False,
            ),
        ],
    )
    def test_readiness_checks_each_configured_mcp_server_session(
        self, sessions, expected_ready
    ):
        class MCPAgent:
            name = "MCPAgent"
            _initialized = True
            mcp_tools = [
                {"name": "server_one", "transport_type": "stdio"},
                {"name": "server_two", "transport_type": "stdio"},
            ]
            mcp_client = SimpleNamespace(sessions=sessions)

            def generate_session_id(self):
                return "mcp-session-state"

            async def connect_mcp_servers(self):
                return None

        server = OmniServe(
            agent=MCPAgent(),
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is expected_ready
        assert resp.json()["mcp_connected"] is expected_ready

    def test_readiness_uses_mcp_requested_to_actual_server_mapping(self):
        class MCPAgent:
            name = "MappedMCPAgent"
            _initialized = True
            mcp_tools = [{"name": "filesystem", "transport_type": "stdio"}]
            mcp_client = SimpleNamespace(
                added_servers_names={"filesystem": "FileSystem MCP"},
                sessions={"FileSystem MCP": {"connected": True}},
            )

            def generate_session_id(self):
                return "mapped-mcp-session"

            async def connect_mcp_servers(self):
                return None

        server = OmniServe(
            agent=MCPAgent(),
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is True
        assert resp.json()["mcp_connected"] is True

    def test_readiness_supports_single_dict_mcp_config(self):
        class MCPAgent:
            name = "DictMCPAgent"
            _initialized = True
            mcp_tools = {"name": "filesystem", "transport_type": "stdio"}
            mcp_client = SimpleNamespace(sessions={"filesystem": {"connected": True}})

            def generate_session_id(self):
                return "dict-mcp-session"

            async def connect_mcp_servers(self):
                return None

        server = OmniServe(
            agent=MCPAgent(),
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is True
        assert resp.json()["mcp_connected"] is True

    def test_readiness_requires_sessions_for_configured_mcp_servers(self):
        class MCPAgent:
            name = "NoSessionMCPAgent"
            _initialized = True
            mcp_tools = [{"name": "filesystem", "transport_type": "stdio"}]
            mcp_client = SimpleNamespace()

            def generate_session_id(self):
                return "no-session-mcp"

            async def connect_mcp_servers(self):
                return None

        server = OmniServe(
            agent=MCPAgent(),
            config=OmniServeConfig(background_enabled=False),
        )

        with TestClient(server.app) as client:
            resp = client.get("/ready")

        assert resp.status_code == 200
        assert resp.json()["ready"] is False
        assert resp.json()["mcp_connected"] is False

    def test_lifespan_cleanup_runs_after_startup_failure(self):
        class FailingStartupAgent:
            name = "FailingStartupAgent"

            def __init__(self):
                self.cleaned = False

            async def connect_mcp_servers(self):
                raise RuntimeError("mcp startup failed")

            async def cleanup(self):
                self.cleaned = True

        agent = FailingStartupAgent()
        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )

        with pytest.raises(RuntimeError, match="mcp startup failed"):
            with TestClient(server.app):
                pass

        assert agent.cleaned is True
        assert server.app.state.omniserve_startup_complete is False

    def test_lifespan_agent_cleanup_runs_when_background_shutdown_fails(self):
        class CleanupAgent:
            name = "CleanupAgent"

            def __init__(self):
                self.cleaned = False

            async def cleanup(self):
                self.cleaned = True

        class FailingShutdownManager:
            async def initialize(self):
                return None

            async def register_agent(self, *args, **kwargs):
                return None

            async def start(self):
                return None

            async def shutdown(self):
                raise RuntimeError("background shutdown failed")

        agent = CleanupAgent()
        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_start_worker=True),
            background_manager=FailingShutdownManager(),
        )

        with pytest.raises(RuntimeError, match="background shutdown failed"):
            with TestClient(server.app):
                assert server.app.state.omniserve_startup_complete is True

        assert agent.cleaned is True
        assert server.app.state.omniserve_startup_complete is False

    def test_openapi_uses_package_version(self, server_client):
        resp = server_client.get("/openapi.json")

        assert resp.status_code == 200
        assert resp.json()["info"]["version"] == version("omnicoreagent")

    def test_sync_run(self, server_client):
        resp = server_client.post("/run/sync", json={"query": "Hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Agent response"
        assert data["agent_name"] == "EndpointTestAgent"

    def test_sync_run_records_serve_request_trace(self, server_client):
        resp = server_client.post("/run/sync", json={"query": "Hello"})

        assert resp.status_code == 200
        traces = asyncio.run(server_client.app.state.agent.telemetry_store.list_traces())
        serve_trace = next(
            trace
            for trace in traces
            if any(span.kind == "serve.request" for span in trace.spans)
        )
        assert serve_trace.session_id == "test-endpoint-session"
        assert serve_trace.run_id == resp.json()["run_id"]
        assert [event.event_type for event in serve_trace.events] == [
            "serve_request_start",
            "serve_request_end",
        ]

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
        assert data["summary"]["event_count"] == 1
        assert data["summary"]["span_count"] == 1
        assert data["steps"][0]["event_type"] == "user_message"

    def test_trace_endpoint_uses_telemetry_trace_accessor(self, server_client):
        server_client.get("/events/trace_like_session/trace")
        agent = server_client.app.state.agent
        agent.get_latest_trace.assert_awaited_with("trace_like_session")

    def test_trace_endpoint_can_filter_by_run_id(self, server_client):
        resp = server_client.get(
            "/events/test-endpoint-session/trace?run_id=run_endpoint"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["trace_id"] == "trace_endpoint_by_run"
        assert data["summary"]["run_id"] == "run_endpoint"
        assert data["steps"][0]["event_type"] == "final_answer"
        agent = server_client.app.state.agent
        agent.get_trace.assert_awaited_with(run_id="run_endpoint")

    def test_trace_endpoint_rejects_run_id_from_another_session(self, server_client):
        agent = server_client.app.state.agent
        agent.get_trace.side_effect = None
        agent.get_trace.return_value = {
            "trace_id": "trace_other",
            "run_id": "run_other",
            "session_id": "other-session",
            "status": "completed",
            "events": [{"event_type": "user_message"}],
            "spans": [{"kind": "agent.run"}],
        }

        resp = server_client.get("/events/test-endpoint-session/trace?run_id=run_other")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-endpoint-session"
        assert data["summary"] == {
            "trace_id": None,
            "run_id": None,
            "status": None,
            "event_count": 0,
            "span_count": 0,
        }
        assert data["steps"] == []

    def test_trace_endpoint_returns_empty_summary_for_missing_run_id(
        self, server_client
    ):
        agent = server_client.app.state.agent
        agent.get_trace.side_effect = None
        agent.get_trace.return_value = None

        resp = server_client.get(
            "/events/test-endpoint-session/trace?run_id=run_missing"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["trace_id"] is None
        assert data["summary"]["run_id"] is None
        assert data["summary"]["event_count"] == 0
        assert data["summary"]["span_count"] == 0
        assert data["steps"] == []

    def test_events_list_endpoint_uses_telemetry_events_accessor(self, server_client):
        resp = server_client.get("/events/test-endpoint-session/list")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-endpoint-session"
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "user_message"
        agent = server_client.app.state.agent
        agent.get_telemetry_events_after.assert_awaited_with(
            cursor=None,
            session_id="test-endpoint-session",
            run_id=None,
        )

    def test_events_list_endpoint_can_filter_by_run_id(self, server_client):
        resp = server_client.get(
            "/events/test-endpoint-session/list?run_id=run_endpoint"
        )

        assert resp.status_code == 200
        agent = server_client.app.state.agent
        agent.get_telemetry_events_after.assert_awaited_with(
            cursor=None,
            session_id="test-endpoint-session",
            run_id="run_endpoint",
        )

    def test_telemetry_events_endpoint_filters_by_session_and_run(self, server_client):
        resp = server_client.get(
            "/telemetry/events?session_id=test-endpoint-session&run_id=run_endpoint"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"] == {
            "limit": 200,
            "run_id": "run_endpoint",
            "session_id": "test-endpoint-session",
        }
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "user_message"
        agent = server_client.app.state.agent
        agent.get_telemetry_events_after.assert_awaited_with(
            cursor=None,
            trace_id=None,
            run_id="run_endpoint",
            session_id="test-endpoint-session",
            task_id=None,
            event_types=None,
        )

    def test_telemetry_traces_endpoint_lists_filtered_traces(self, server_client):
        resp = server_client.get(
            "/telemetry/traces?session_id=test-endpoint-session&run_id=run_endpoint"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"]["session_id"] == "test-endpoint-session"
        assert data["filters"]["run_id"] == "run_endpoint"
        assert data["filters"]["limit"] == 100
        assert data["count"] == 1
        assert data["traces"][0]["trace_id"] == "trace_endpoint"
        agent = server_client.app.state.agent
        agent.list_telemetry_traces.assert_awaited_with(
            trace_id=None,
            run_id="run_endpoint",
            session_id="test-endpoint-session",
            task_id=None,
            agent_id=None,
            workflow_id=None,
            model=None,
            status=None,
            normalize=False,
        )

    def test_telemetry_trace_endpoint_uses_exact_trace_id(self, server_client):
        resp = server_client.get("/telemetry/traces/trace_endpoint")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"] == {"trace_id": "trace_endpoint", "normalize": False}
        assert data["summary"]["trace_id"] == "trace_endpoint"
        assert data["trace"]["events"][0]["event_type"] == "final_answer"
        agent = server_client.app.state.agent
        agent.get_trace.assert_awaited_with(trace_id="trace_endpoint", normalize=False)

    def test_telemetry_run_trace_endpoint_uses_run_id(self, server_client):
        resp = server_client.get("/telemetry/runs/run_endpoint/trace")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"] == {"run_id": "run_endpoint", "normalize": False}
        assert data["summary"]["run_id"] == "run_endpoint"
        agent = server_client.app.state.agent
        agent.get_trace.assert_awaited_with(run_id="run_endpoint", normalize=False)

    def test_telemetry_session_trace_endpoint_uses_latest_session_trace(
        self, server_client
    ):
        resp = server_client.get("/telemetry/sessions/test-endpoint-session/trace")

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"] == {
            "session_id": "test-endpoint-session",
            "normalize": False,
        }
        assert data["summary"]["trace_id"] == "trace_endpoint"
        agent = server_client.app.state.agent
        agent.get_latest_trace.assert_awaited_with(
            session_id="test-endpoint-session",
            normalize=False,
        )

    def test_telemetry_trace_detail_endpoints_return_404_when_missing(self):
        class MissingTraceAgent:
            name = "MissingTraceAgent"

            def generate_session_id(self):
                return "missing-trace-session"

            async def get_trace(self, **kwargs):
                return None

            async def get_latest_trace(self, **kwargs):
                return None

        server = OmniServe(
            agent=MissingTraceAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        assert client.get("/telemetry/traces/missing").status_code == 404
        assert client.get("/telemetry/runs/missing/trace").status_code == 404
        assert client.get("/telemetry/sessions/missing/trace").status_code == 404

    def test_telemetry_trace_detail_endpoints_reject_mismatched_accessor_result(self):
        class MismatchedTraceAgent:
            name = "MismatchedTraceAgent"

            def generate_session_id(self):
                return "requested-session"

            async def get_trace(self, **kwargs):
                return {
                    "trace_id": "wrong-trace",
                    "run_id": "wrong-run",
                    "session_id": "wrong-session",
                    "status": "completed",
                    "events": [],
                    "spans": [],
                }

            async def get_latest_trace(self, **kwargs):
                return {
                    "trace_id": "wrong-trace",
                    "run_id": "wrong-run",
                    "session_id": "wrong-session",
                    "status": "completed",
                    "events": [],
                    "spans": [],
                }

        server = OmniServe(
            agent=MismatchedTraceAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        assert client.get("/telemetry/traces/requested-trace").status_code == 404
        assert client.get("/telemetry/runs/requested-run/trace").status_code == 404
        assert client.get("/telemetry/sessions/requested-session/trace").status_code == 404

    def test_telemetry_traces_endpoint_rejects_invalid_status(self, server_client):
        resp = server_client.get("/telemetry/traces?status=bogus")

        assert resp.status_code == 422
        assert "Invalid trace status" in resp.json()["detail"]

    def test_telemetry_events_endpoint_honors_limit(self, server_client):
        resp = server_client.get(
            "/telemetry/events?session_id=test-endpoint-session&limit=1"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["filters"]["limit"] == 1
        assert data["count"] == 1

    def test_telemetry_events_stream_endpoint_is_registered_and_run_filtered(self):
        class StreamTelemetryAgent:
            name = "StreamTelemetryAgent"

            def generate_session_id(self):
                return "stream-session"

            def get_telemetry_stream_cursor(self, *, session_id, run_id):
                assert session_id == "stream-session"
                assert run_id == "run-stream"
                return None

            def get_telemetry_events_after(self, *, cursor, session_id, run_id):
                assert cursor is None
                assert session_id == "stream-session"
                assert run_id == "run-stream"
                return [
                    {
                        "event_type": "final_answer",
                        "metadata": {"session_id": session_id, "run_id": "run-stream"},
                    },
                    {
                        "event_type": "final_answer",
                        "metadata": {"session_id": session_id, "run_id": "run-drop"},
                    },
                ]

            async def stream_telemetry_after(self, *, cursor, session_id, run_id):
                if False:
                    yield {}

        server = OmniServe(
            agent=StreamTelemetryAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        resp = client.get(
            "/telemetry/events/stream?session_id=stream-session&run_id=run-stream"
        )

        assert resp.status_code == 200
        assert "run-stream" in resp.text
        assert "run-drop" not in resp.text

    def test_events_list_endpoint_defensively_filters_run_id(self):
        class UnfilteredTelemetryAgent:
            name = "UnfilteredTelemetryAgent"

            def generate_session_id(self):
                return "unfiltered-session"

            def get_telemetry_events_after(self, *, cursor, session_id, run_id):
                assert cursor is None
                assert session_id == "unfiltered-session"
                assert run_id == "run-keep"
                return [
                    {
                        "event_type": "final_answer",
                        "metadata": {"run_id": "run-keep"},
                    },
                    {
                        "event_type": "final_answer",
                        "metadata": {"run_id": "run-drop"},
                    },
                ]

        server = OmniServe(
            agent=UnfilteredTelemetryAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        resp = client.get("/events/unfiltered-session/list?run_id=run-keep")

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["metadata"]["run_id"] == "run-keep"

    def test_telemetry_events_endpoint_defensively_filters_run_id(self):
        class UnfilteredTelemetryAgent:
            name = "TelemetryUnfilteredAgent"

            def generate_session_id(self):
                return "telemetry-unfiltered-session"

            def get_telemetry_events_after(self, *, cursor, session_id, run_id):
                assert cursor is None
                assert session_id == "telemetry-unfiltered-session"
                assert run_id == "run-keep"
                return [
                    {
                        "event_type": "tool_result",
                        "metadata": {
                            "session_id": session_id,
                            "run_id": "run-keep",
                        },
                    },
                    {
                        "event_type": "tool_result",
                        "metadata": {
                            "session_id": session_id,
                            "run_id": "run-drop",
                        },
                    },
                ]

        server = OmniServe(
            agent=UnfilteredTelemetryAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        resp = client.get(
            "/telemetry/events?session_id=telemetry-unfiltered-session&run_id=run-keep"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["metadata"]["run_id"] == "run-keep"

    def test_sync_telemetry_accessors_are_supported_by_json_endpoints(self):
        class SyncTelemetryAgent:
            name = "SyncTelemetryAgent"

            def generate_session_id(self):
                return "sync-telemetry-session"

            def get_telemetry_events_after(self, *, cursor, session_id, run_id):
                assert cursor is None
                return [
                    {
                        "event_type": "user_message",
                        "metadata": {"session_id": session_id, "run_id": run_id},
                    }
                ]

            def get_latest_trace(self, session_id):
                return {
                    "trace_id": "trace-sync-latest",
                    "run_id": "run-sync-latest",
                    "session_id": session_id,
                    "status": "completed",
                    "events": [{"event_type": "user_message"}],
                    "spans": [{"kind": "agent.run"}],
                }

            def get_trace(self, *, run_id):
                return {
                    "trace_id": "trace-sync-run",
                    "run_id": run_id,
                    "session_id": "sync-telemetry-session",
                    "status": "completed",
                    "events": [{"event_type": "final_answer"}],
                    "spans": [{"kind": "agent.run"}],
                }

        server = OmniServe(
            agent=SyncTelemetryAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        events = client.get("/events/sync-telemetry-session/list?run_id=run-sync")
        latest_trace = client.get("/events/sync-telemetry-session/trace")
        run_trace = client.get("/events/sync-telemetry-session/trace?run_id=run-sync")

        assert events.status_code == 200
        assert events.json()["events"][0]["metadata"]["run_id"] == "run-sync"
        assert latest_trace.status_code == 200
        assert latest_trace.json()["summary"]["trace_id"] == "trace-sync-latest"
        assert run_trace.status_code == 200
        assert run_trace.json()["summary"]["trace_id"] == "trace-sync-run"

    def test_telemetry_json_endpoints_return_empty_for_lightweight_agents(self):
        class LightweightAgent:
            name = "LightweightTelemetryAgent"

            def generate_session_id(self):
                return "lightweight-session"

            async def run(self, query, *, session_id):
                return {"response": f"ok:{query}", "session_id": session_id}

        server = OmniServe(
            agent=LightweightAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        events = client.get("/events/lightweight-session/list")
        trace = client.get("/events/lightweight-session/trace")

        assert events.status_code == 200
        assert events.json() == {
            "session_id": "lightweight-session",
            "events": [],
            "count": 0,
        }
        assert trace.status_code == 200
        assert trace.json() == {
            "session_id": "lightweight-session",
            "summary": {
                "trace_id": None,
                "run_id": None,
                "status": None,
                "event_count": 0,
                "span_count": 0,
            },
            "steps": [],
        }

    def test_session_history_endpoints_tolerate_lightweight_agents(self):
        class LightweightAgent:
            name = "LightweightHistoryAgent"

            def generate_session_id(self):
                return "lightweight-history-session"

        server = OmniServe(
            agent=LightweightAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        history = client.get("/sessions/lightweight-history-session/history")
        cleared = client.delete("/sessions/lightweight-history-session")

        assert history.status_code == 200
        assert history.json() == {
            "session_id": "lightweight-history-session",
            "messages": [],
            "count": 0,
        }
        assert cleared.status_code == 200
        assert cleared.json() == {
            "status": "cleared",
            "session_id": "lightweight-history-session",
        }

    def test_run_sse_route_streams_for_lightweight_agents(self):
        class LightweightAgent:
            name = "LightweightSseAgent"

            def generate_session_id(self):
                return "lightweight-sse-session"

            async def run(self, query, *, session_id):
                return {"response": f"streamed:{query}", "session_id": session_id}

        server = OmniServe(
            agent=LightweightAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        with client.stream("POST", "/run", json={"query": "hello"}) as response:
            body = "".join(response.iter_text())

        assert response.status_code == 200
        assert "event: session" in body
        assert "event: complete" in body
        assert "streamed:hello" in body

    def test_omniserve_sync_run_populates_telemetry_endpoints(self):
        class TelemetryAgent:
            name = "TelemetryHttpAgent"

            def __init__(self):
                self.store = InMemoryTelemetryStore()
                self.telemetry_stream = TelemetryStream(self.store)

            def generate_session_id(self):
                return "telemetry-http-session"

            async def run(self, query, *, session_id, run_id):
                recorder = TelemetryRecorder(self.store)
                context = await recorder.start_trace(
                    name="agent.run",
                    kind="agent.run",
                    actor=TelemetryActor(type=ActorType.AGENT, name=self.name),
                    session_id=session_id,
                    run_id=run_id,
                )
                await recorder.emit_event(
                    "user_message",
                    actor=TelemetryActor(type=ActorType.USER),
                    input={"message": query},
                )
                await recorder.emit_event(
                    "final_answer",
                    actor=TelemetryActor(type=ActorType.AGENT, name=self.name),
                    output={"response": f"ok:{query}"},
                )
                await recorder.end_trace(output={"response": f"ok:{query}"})
                return {
                    "response": f"ok:{query}",
                    "session_id": session_id,
                    "run_id": run_id,
                    "trace_id": context.trace_id,
                }

            async def get_telemetry_events_after(self, *, cursor, session_id, run_id):
                stream = TelemetryStream(self.store)
                return await stream.get_events_after(
                    TelemetryStreamScope(session_id=session_id, run_id=run_id),
                    cursor,
                )

            async def get_latest_trace(self, session_id):
                traces = await self.store.list_traces()
                traces = [trace for trace in traces if trace.session_id == session_id]
                return traces[-1].model_dump() if traces else None

            async def get_trace(self, *, run_id):
                traces = await self.store.list_traces()
                traces = [trace for trace in traces if trace.run_id == run_id]
                return traces[-1].model_dump() if traces else None

        server = OmniServe(
            agent=TelemetryAgent(),
            config=OmniServeConfig(background_enabled=False),
        )
        client = TestClient(server.app, raise_server_exceptions=False)

        run = client.post(
            "/run/sync",
            json={"query": "hello", "session_id": "telemetry-http-session"},
        )
        assert run.status_code == 200
        run_id = run.json()["run_id"]

        events = client.get(f"/events/telemetry-http-session/list?run_id={run_id}")
        trace = client.get(f"/events/telemetry-http-session/trace?run_id={run_id}")

        assert events.status_code == 200
        event_types = {event["event_type"] for event in events.json()["events"]}
        assert {"user_message", "final_answer", "serve_request_start"}.issubset(
            event_types
        )
        assert trace.status_code == 200
        assert trace.json()["summary"]["run_id"] == run_id

    def test_run_normalizes_string_agent_result(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "StringAgent"
        agent.generate_session_id.return_value = "string-session"
        agent.run = AsyncMock(return_value="plain response")

        server = OmniServe(agent=agent, config=OmniServeConfig())
        client = TestClient(server.app)

        resp = client.post("/run/sync", json={"query": "Hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"].startswith("run_")
        assert data == {
            "response": "plain response",
            "session_id": "string-session",
            "agent_name": "StringAgent",
            "metric": None,
            "trace_id": None,
            "run_id": data["run_id"],
        }

    def test_sync_run_ignores_non_telemetry_business_store(self):
        class LightweightAgent:
            name = "BusinessStoreAgent"

            def __init__(self):
                self.store = {"business": "state"}

            def generate_session_id(self):
                return "business-store-session"

            async def run(self, query, *, session_id):
                return {"response": f"ok:{query}", "session_id": session_id}

        server = OmniServe(agent=LightweightAgent(), config=OmniServeConfig())
        client = TestClient(server.app)

        resp = client.post("/run/sync", json={"query": "Hello"})

        assert resp.status_code == 200
        assert resp.json()["response"] == "ok:Hello"

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

    def test_unhandled_route_errors_return_stable_json(self):
        agent = MagicMock(spec=OmniCoreAgent)
        agent.name = "ErrorAgent"
        agent.generate_session_id.return_value = "error-session"
        server = OmniServe(
            agent=agent,
            config=OmniServeConfig(background_enabled=False),
        )

        @server.app.get("/boom")
        async def boom():
            raise RuntimeError("route exploded")

        client = TestClient(server.app, raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code == 500
        assert resp.json() == {
            "error": "InternalServerError",
            "message": "An internal server error occurred",
            "detail": "route exploded",
        }

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
