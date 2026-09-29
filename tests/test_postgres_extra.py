"""R6 (0.5.0rc1 gate): `postgresql://`, as the docs write it, works on a fresh install.

The postgres extra shipped psycopg2 only. SQLAlchemy 2.0 uses it for
`postgresql://`; SQLAlchemy 2.1, which a fresh install resolved to, uses
psycopg 3 by default, so MemoryRouter("sql") failed with "No module named
'psycopg'". CI missed it: the lockfile pins 2.0. The extra now ships both
drivers, so the URL works whichever SQLAlchemy is installed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_postgres_extra_ships_the_driver_each_sqlalchemy_uses_by_default():
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    for name in ("postgres", "all"):
        names = {requirement.split("[")[0].split(">")[0].split("=")[0].strip() for requirement in extras[name]}
        assert {"psycopg2-binary", "psycopg"} <= names, (name, names)


@pytest.mark.parametrize("driver", ["postgresql+psycopg2", "postgresql+psycopg", "postgresql"])
def test_each_postgres_url_form_finds_its_driver(driver):
    from sqlalchemy import create_engine

    # No connection is made: this only loads the dialect and its driver.
    create_engine(f"{driver}://user:secret@localhost:5432/db").dispose()
