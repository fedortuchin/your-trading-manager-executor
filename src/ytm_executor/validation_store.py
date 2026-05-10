"""Local sanitized broker credential validation status storage."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from ytm_executor.guards import reject_secret_fields
from ytm_executor.state import DEFAULT_HOME

DEFAULT_VALIDATIONS_FILE = DEFAULT_HOME / "validations.json"


class LocalValidationStore:
    """Stores non-secret validation summaries for heartbeat and local inspection."""

    def __init__(self, *, validations_file: Path = DEFAULT_VALIDATIONS_FILE) -> None:
        self._validations_file = validations_file

    def put(self, summary: dict[str, Any]) -> None:
        reject_secret_fields(summary)
        provider = _required_text(summary.get("provider"), "provider")
        name = _required_text(summary.get("name"), "name")
        records = [
            item
            for item in _load_records(self._validations_file)
            if not (item.get("provider") == provider and item.get("name") == name)
        ]
        records.append(dict(summary))
        _write_json_private(self._validations_file, records)

    def list_public(self) -> tuple[dict[str, Any], ...]:
        records = []
        for item in _load_records(self._validations_file):
            public_item = dict(item)
            reject_secret_fields(public_item)
            records.append(public_item)
        return tuple(records)


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("validations file must contain a list")
    return [dict(item) for item in value if isinstance(item, dict)]


def _write_json_private(path: Path, value: object) -> None:
    reject_secret_fields(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text

