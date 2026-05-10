"""Local executor state."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_HOME = Path.home() / ".ytm-executor"
DEFAULT_STATE_FILE = DEFAULT_HOME / "state.json"


@dataclass(frozen=True, slots=True)
class ExecutorState:
    server_url: str
    access_token: str
    executor_id: str
    allowed_hosts: tuple[str, ...]


def write_state(path: Path, state: ExecutorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "accessToken": state.access_token,
        "allowedHosts": list(state.allowed_hosts),
        "executorId": state.executor_id,
        "serverUrl": state.server_url,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def read_state(path: Path) -> ExecutorState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("executor state must be a JSON object")
    allowed_hosts = payload.get("allowedHosts")
    if not isinstance(allowed_hosts, list) or not all(
        isinstance(item, str) for item in allowed_hosts
    ):
        raise ValueError("executor state allowedHosts is invalid")
    return ExecutorState(
        access_token=_expect_text(payload, "accessToken"),
        allowed_hosts=tuple(allowed_hosts),
        executor_id=_expect_text(payload, "executorId"),
        server_url=_expect_text(payload, "serverUrl").rstrip("/"),
    )


def expect_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} is missing")
    return value


def _expect_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is missing")
    return value
