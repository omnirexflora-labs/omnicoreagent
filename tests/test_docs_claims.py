from pathlib import Path


DOC_ROOTS = [
    Path("README.md"),
    Path("cookbook"),
    Path("docs"),
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
            if path.suffix in {".md", ".mdx", ".py"}:
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


def test_omniserve_docs_include_all_public_env_vars():
    expected = {
        "OMNISERVE_HOST",
        "OMNISERVE_PORT",
        "OMNISERVE_WORKERS",
        "OMNISERVE_API_PREFIX",
        "OMNISERVE_ENABLE_DOCS",
        "OMNISERVE_ENABLE_REDOC",
        "OMNISERVE_CORS_ENABLED",
        "OMNISERVE_CORS_ORIGINS",
        "OMNISERVE_CORS_METHODS",
        "OMNISERVE_CORS_HEADERS",
        "OMNISERVE_CORS_CREDENTIALS",
        "OMNISERVE_AUTH_ENABLED",
        "OMNISERVE_AUTH_TOKEN",
        "OMNISERVE_REQUEST_LOGGING",
        "OMNISERVE_LOG_LEVEL",
        "OMNISERVE_REQUEST_TIMEOUT",
        "OMNISERVE_RATE_LIMIT_ENABLED",
        "OMNISERVE_RATE_LIMIT_REQUESTS",
        "OMNISERVE_RATE_LIMIT_WINDOW",
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
