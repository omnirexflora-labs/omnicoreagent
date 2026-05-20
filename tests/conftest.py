from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def pytest_configure(config):
    """Expose local live-backend test URLs without importing unrelated env."""
    env_values: dict[str, str] = {}
    for path in (Path("src/.env"), Path(".env")):
        if not path.exists():
            continue
        env_values.update(
            {
                key: value
                for key, value in dotenv_values(path).items()
                if value is not None
            }
        )

    redis_url = env_values.get("REDIS_URL")
    if redis_url:
        os.environ.setdefault("REDIS_URL", redis_url)
        os.environ.setdefault("OMNICOREAGENT_TEST_REDIS_URL", redis_url)

    mongodb_uri = env_values.get("MONGODB_URI")
    if mongodb_uri:
        os.environ.setdefault("MONGODB_URI", mongodb_uri)
        os.environ.setdefault("OMNICOREAGENT_TEST_MONGODB_URI", mongodb_uri)

    mongodb_database = env_values.get("MONGODB_DB_NAME")
    if mongodb_database:
        os.environ.setdefault("MONGODB_DB_NAME", mongodb_database)
        os.environ.setdefault("OMNICOREAGENT_TEST_MONGODB_DATABASE", mongodb_database)
