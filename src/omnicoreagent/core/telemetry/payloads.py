from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from omnicoreagent.core.workspace.storage import WorkspaceStorage


_PAYLOAD_REFERENCE_PREFIX = "telemetry://payload/"
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class TelemetryPayloadStore(Protocol):
    """Storage contract for redacted oversized telemetry payloads."""

    def write(
        self,
        payload: Any,
        *,
        checksum: str,
        content_type: str = "application/json",
    ) -> str: ...

    def read(self, reference: str) -> Any: ...

    def prune(
        self,
        retention_days: int | None = None,
        *,
        references: set[str] | None = None,
    ) -> int: ...


class WorkspaceTelemetryPayloadStore:
    """Content-addressed payload storage over a workspace storage adapter."""

    def __init__(
        self,
        storage: WorkspaceStorage,
        *,
        retention_days: int | None = 7,
    ) -> None:
        self.storage = storage
        self.retention_days = retention_days

    def write(
        self,
        payload: Any,
        *,
        checksum: str,
        content_type: str = "application/json",
    ) -> str:
        _validate_checksum(checksum)
        record = {
            "checksum": checksum,
            "content_type": content_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        self.storage.write_text(
            _payload_key(checksum),
            json.dumps(record, sort_keys=True, default=str),
        )
        return _payload_reference(checksum)

    def read(self, reference: str) -> Any:
        checksum = _checksum_from_reference(reference)
        record = json.loads(self.storage.read_text(_payload_key(checksum)))
        if record.get("checksum") != checksum:
            raise ValueError("Telemetry payload checksum does not match reference")
        return record.get("payload")

    def prune(
        self,
        retention_days: int | None = None,
        *,
        references: set[str] | None = None,
    ) -> int:
        """Remove expired payloads while retaining explicitly referenced files."""
        days = self.retention_days if retention_days is None else retention_days
        if days is None:
            return 0
        if days < 0:
            raise ValueError("telemetry payload retention_days must be non-negative or None")

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        retained_checksums = {
            _checksum_from_reference(reference)
            for reference in (references or set())
            if reference.startswith(_PAYLOAD_REFERENCE_PREFIX)
        }
        removed = 0
        for item in self.storage.list_files():
            if not item.name.endswith(".json"):
                continue
            checksum = item.name.removesuffix(".json")
            if not _CHECKSUM_PATTERN.fullmatch(checksum):
                continue
            if checksum in retained_checksums:
                continue
            modified_at = item.modified_at
            if modified_at.tzinfo is None:
                modified_at = modified_at.replace(tzinfo=timezone.utc)
            if modified_at >= cutoff:
                continue
            self.storage.delete(item.path)
            removed += 1
        return removed


class LocalTelemetryPayloadStore(WorkspaceTelemetryPayloadStore):
    """Payload store rooted at a local directory outside the workspace API."""

    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int | None = 7,
    ) -> None:
        from omnicoreagent.core.workspace.storage import LocalWorkspaceStorage

        super().__init__(
            LocalWorkspaceStorage(root),
            retention_days=retention_days,
        )


def _payload_key(checksum: str) -> str:
    _validate_checksum(checksum)
    return f"{checksum}.json"


def _payload_reference(checksum: str) -> str:
    _validate_checksum(checksum)
    return f"{_PAYLOAD_REFERENCE_PREFIX}{checksum}"


def _checksum_from_reference(reference: str) -> str:
    if not isinstance(reference, str) or not reference.startswith(
        _PAYLOAD_REFERENCE_PREFIX
    ):
        raise ValueError("Invalid telemetry payload reference")
    checksum = reference.removeprefix(_PAYLOAD_REFERENCE_PREFIX)
    _validate_checksum(checksum)
    return checksum


def _validate_checksum(checksum: str) -> None:
    if not isinstance(checksum, str) or not _CHECKSUM_PATTERN.fullmatch(checksum):
        raise ValueError("Telemetry payload checksum must be a SHA-256 hex digest")
