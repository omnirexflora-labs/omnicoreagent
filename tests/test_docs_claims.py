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
