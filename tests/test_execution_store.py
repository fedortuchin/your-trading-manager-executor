from __future__ import annotations

from pathlib import Path

import pytest

from ytm_executor.execution_store import LocalExecutionStore


def test_execution_store_persists_sanitized_real_result(tmp_path: Path) -> None:
    store = LocalExecutionStore(tmp_path / "executions.json")

    store.put(
        command_id="command-1",
        status="acknowledged",
        result_payload={
            "executorAction": "order_submitted",
            "providerOrderId": "okx-order-1",
            "zeroSecret": True,
        },
    )

    stored = store.get("command-1")
    assert stored is not None
    assert stored.status == "acknowledged"
    assert stored.result_payload["providerOrderId"] == "okx-order-1"
    assert (tmp_path / "executions.json").stat().st_mode & 0o777 == 0o600


def test_execution_store_rejects_secret_like_result(tmp_path: Path) -> None:
    store = LocalExecutionStore(tmp_path / "executions.json")

    with pytest.raises(Exception, match="secret"):
        store.put(
            command_id="command-1",
            status="acknowledged",
            result_payload={"apiSecret": "must-not-persist"},
        )
