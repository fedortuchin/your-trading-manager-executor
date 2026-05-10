from __future__ import annotations

from pathlib import Path

from ytm_executor.secret_store import LocalSecretStore


def test_secret_store_keeps_public_summary_without_secret_values(tmp_path: Path) -> None:
    store = LocalSecretStore(
        key_file=tmp_path / "master.key",
        secrets_file=tmp_path / "secrets.json",
    )
    store.put(
        provider="binance",
        name="main",
        secret={"apiKey": "public-key", "apiSecret": "private-secret"},
    )

    assert store.list()[0].provider == "binance"
    assert store.heartbeat_capability() == {
        "localCredentials": [{"name": "main", "provider": "binance"}]
    }
    assert "private-secret" not in (tmp_path / "secrets.json").read_text(encoding="utf-8")
