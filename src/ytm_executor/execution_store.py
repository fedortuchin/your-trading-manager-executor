"""Durable local execution-result cache for real commands."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ytm_executor.guards import reject_secret_fields


@dataclass(frozen=True, slots=True)
class StoredExecutionResult:
    command_id: str
    result_payload: dict[str, Any]
    status: str
    updated_at: str


class LocalExecutionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, command_id: str) -> StoredExecutionResult | None:
        records = self._read()
        item = records.get(command_id)
        if not isinstance(item, dict):
            return None
        result_payload = item.get("resultPayload")
        status = item.get("status")
        updated_at = item.get("updatedAt")
        if not isinstance(result_payload, dict) or not isinstance(status, str):
            return None
        if not isinstance(updated_at, str):
            updated_at = ""
        reject_secret_fields(result_payload)
        return StoredExecutionResult(
            command_id=command_id,
            result_payload=dict(result_payload),
            status=status,
            updated_at=updated_at,
        )

    def put(self, *, command_id: str, status: str, result_payload: dict[str, Any]) -> None:
        reject_secret_fields(result_payload)
        records = self._read()
        records[command_id] = {
            "resultPayload": result_payload,
            "status": status,
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        value = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("execution store must contain a JSON object")
        reject_secret_fields(value)
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}
