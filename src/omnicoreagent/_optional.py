"""Helpers for optional dependency boundaries."""

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def optional_dependency_error(feature: str, extra: str, exc: ModuleNotFoundError) -> ImportError:
    package = exc.name or "required package"
    return ImportError(
        f"{feature} requires optional dependency '{package}'. "
        f"Install it with: pip install 'omnicoreagent[{extra}]'"
    )


def load_optional(feature: str, extra: str, loader: Callable[[], T]) -> T:
    try:
        return loader()
    except ModuleNotFoundError as exc:
        raise optional_dependency_error(feature, extra, exc) from exc
