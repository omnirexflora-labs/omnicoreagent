from pathlib import Path


DOC_ROOTS = [
    Path("README.md"),
    Path("cookbook"),
    Path("docs"),
    Path("docker"),
]

FORBIDDEN_PUBLIC_API_KEY_NAMES = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
}


def _iter_user_facing_files():
    for root in DOC_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.name in {"Dockerfile", ".env.example"} or path.suffix in {
                ".md",
                ".mdx",
                ".py",
                ".yml",
                ".yaml",
            }:
                yield path


def test_user_facing_docs_use_single_llm_api_key_name():
    offenders = []
    for path in _iter_user_facing_files():
        if path.as_posix() == "docs/changelog.mdx":
            continue
        text = path.read_text(encoding="utf-8")
        found = sorted(name for name in FORBIDDEN_PUBLIC_API_KEY_NAMES if name in text)
        if found:
            offenders.append(f"{path}: {', '.join(found)}")

    assert not offenders, (
        "User-facing docs and cookbook examples must expose LLM_API_KEY as the "
        "single public LLM credential variable:\n" + "\n".join(offenders)
    )


def test_user_facing_docs_do_not_use_stale_omniserve_prefixes():
    stale_prefixes = {"OMNISERVE_", "OMNISERVER_"}
    offenders = []
    for path in _iter_user_facing_files():
        text = path.read_text(encoding="utf-8")
        found = sorted(prefix for prefix in stale_prefixes if prefix in text)
        if found:
            offenders.append(f"{path}: {', '.join(found)}")

    assert not offenders, (
        "User-facing docs and Docker examples must use OMNICOREAGENT_SERVE_* "
        "or OMNICOREAGENT_BACKGROUND_* env prefixes:\n" + "\n".join(offenders)
    )


def test_omniserve_docs_include_all_public_env_vars():
    expected = {
        "OMNICOREAGENT_SERVE_HOST",
        "OMNICOREAGENT_SERVE_PORT",
        "OMNICOREAGENT_SERVE_WORKERS",
        "OMNICOREAGENT_SERVE_API_PREFIX",
        "OMNICOREAGENT_SERVE_ENABLE_DOCS",
        "OMNICOREAGENT_SERVE_ENABLE_REDOC",
        "OMNICOREAGENT_SERVE_CORS_ENABLED",
        "OMNICOREAGENT_SERVE_CORS_ORIGINS",
        "OMNICOREAGENT_SERVE_CORS_METHODS",
        "OMNICOREAGENT_SERVE_CORS_HEADERS",
        "OMNICOREAGENT_SERVE_CORS_CREDENTIALS",
        "OMNICOREAGENT_SERVE_AUTH_ENABLED",
        "OMNICOREAGENT_SERVE_AUTH_TOKEN",
        "OMNICOREAGENT_SERVE_REQUEST_LOGGING",
        "OMNICOREAGENT_SERVE_LOG_LEVEL",
        "OMNICOREAGENT_SERVE_REQUEST_TIMEOUT",
        "OMNICOREAGENT_SERVE_RATE_LIMIT_ENABLED",
        "OMNICOREAGENT_SERVE_RATE_LIMIT_REQUESTS",
        "OMNICOREAGENT_SERVE_RATE_LIMIT_WINDOW",
        "OMNICOREAGENT_BACKGROUND_ENABLED",
        "OMNICOREAGENT_BACKGROUND_AGENT_ID",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_URI",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_PREFIX",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_COLLECTION_PREFIX",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_CONNECT_TIMEOUT",
        "OMNICOREAGENT_BACKGROUND_START_WORKER",
    }
    docs = {
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("docs/how-to-guides/configuration.mdx"),
        Path("cookbook/omniserve/README.mdx"),
    }

    for path in docs:
        text = path.read_text(encoding="utf-8")
        missing = sorted(name for name in expected if name not in text)
        assert not missing, f"{path} is missing OmniServe env vars: {missing}"


def test_background_docs_do_not_claim_sql_is_only_durable_store():
    docs = [
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("docs/how-to-guides/configuration.mdx"),
        Path("docs/core-concepts/background-agents.mdx"),
        Path("cookbook/background_agents/README.mdx"),
        Path("cookbook/omniserve/README.mdx"),
    ]
    forbidden = [
        "choose `sql` when",
        'task_store="sql"` when',
        "State is restart-persistent when the task store is SQL.",
    ]

    offenders = []
    for path in docs:
        text = path.read_text(encoding="utf-8")
        found = [phrase for phrase in forbidden if phrase in text]
        if found:
            offenders.append(f"{path}: {', '.join(found)}")

    assert not offenders, (
        "Background docs must present sql, redis, and mongodb as durable "
        "task-store choices:\n" + "\n".join(offenders)
    )


def test_background_docs_show_optional_extras_for_remote_task_stores():
    docs = [
        Path("docs/core-concepts/background-agents.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert 'omnicoreagent[redis]' in text
        assert 'omnicoreagent[mongodb]' in text


def test_background_docs_use_current_event_vocabulary():
    stale_events = {
        "background_task_started",
        "background_task_completed",
        "background_task_error",
    }
    docs = [
        Path("docs/core-concepts/events.mdx"),
        Path("docs/core-concepts/background-agents.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]

    offenders = []
    for path in docs:
        text = path.read_text(encoding="utf-8")
        found = sorted(event for event in stale_events if event in text)
        if found:
            offenders.append(f"{path}: {', '.join(found)}")

    assert not offenders, (
        "Background docs must use current run lifecycle events and "
        "background_agent_status, not stale background task event names:\n"
        + "\n".join(offenders)
    )

    events_doc = Path("docs/core-concepts/events.mdx").read_text(encoding="utf-8")
    for expected in {
        "background_agent_status",
        "background_task_scheduled",
        "background_run_queued",
        "background_run_claimed",
        "background_run_started",
        "background_run_completed",
    }:
        assert expected in events_doc


def test_background_cookbook_uses_typed_event_payload_access():
    text = Path("cookbook/background_agents/README.mdx").read_text(encoding="utf-8")

    assert 'event.payload["event"]' not in text
    assert "event.payload.event" in text


def test_ci_enforces_live_background_task_store_backends():
    workflow = Path(".github/workflows/python-app.yml").read_text(encoding="utf-8")

    for expected in {
        "redis:7-alpine",
        "mongo:7",
        "OMNICOREAGENT_TEST_REDIS_URL",
        "OMNICOREAGENT_TEST_MONGODB_URI",
        "OMNICOREAGENT_TEST_MONGODB_DATABASE",
        "Run live background manager backend tests",
        "tests/test_background_durable_manager_integration.py",
    }:
        assert expected in workflow


def test_background_docs_explain_durable_restart_behavior():
    docs = [
        Path("docs/core-concepts/background-agents.mdx"),
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "queued run" in text
        assert "same task store" in text or (
            "same SQL, Redis, or" in text and "MongoDB task store" in text
        )
        assert "in-memory" in text.lower()


def test_background_docs_show_public_status_and_run_endpoints():
    docs = [
        Path("docs/core-concepts/background-agents.mdx"),
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]
    endpoints = {
        "/background/status",
        "/background/tasks/",
        "/background/runs/$RUN_ID",
        "/background/runs/$RUN_ID/events",
        "/background/runs/$RUN_ID/workspace",
    }

    for path in docs:
        text = path.read_text(encoding="utf-8")
        missing = sorted(endpoint for endpoint in endpoints if endpoint not in text)
        assert not missing, f"{path} is missing background endpoints: {missing}"
        assert "curl http://localhost:8000/background/runs/{run_id}" not in text


def test_background_task_store_env_examples_are_documented():
    docs = [
        Path("docs/how-to-guides/configuration.mdx"),
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]
    expected = {
        "OMNICOREAGENT_BACKGROUND_TASK_STORE=sql",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL=sqlite:///.omnicoreagent/background.db",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE=redis",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_URL=redis://localhost:6379/0",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE=mongodb",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_URI=mongodb://localhost:27017",
        "OMNICOREAGENT_BACKGROUND_TASK_STORE_DATABASE=omnicoreagent",
    }

    for path in docs:
        text = path.read_text(encoding="utf-8")
        missing = sorted(name for name in expected if name not in text)
        assert not missing, f"{path} is missing task-store env examples: {missing}"
        assert "Pick one backend" in text or ("Pick" in text and "one backend" in text)


def test_background_docs_include_durable_backend_selection_guidance():
    docs = [
        Path("docs/core-concepts/background-agents.mdx"),
        Path("docs/how-to-guides/configuration.mdx"),
        Path("docs/how-to-guides/omniserve.mdx"),
        Path("cookbook/background_agents/README.mdx"),
    ]
    expected = {
        "Use SQL/SQLite for local durability",
        "Use Redis when your deployment already operates",
        "Use MongoDB when",
        "MongoDB is your durable operational store",
    }

    for path in docs:
        text = path.read_text(encoding="utf-8")
        missing = sorted(phrase for phrase in expected if phrase not in text)
        assert not missing, f"{path} is missing backend selection guidance: {missing}"
