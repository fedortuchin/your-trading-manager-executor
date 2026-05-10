from __future__ import annotations

from pathlib import Path

import pytest

from ytm_executor.guards import SecretFieldError
from ytm_executor.secret_store import LocalSecretStore
from ytm_executor.validation_store import LocalValidationStore


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
    assert store.get(provider="binance", name="main") == {
        "apiKey": "public-key",
        "apiSecret": "private-secret",
    }


def test_validation_store_keeps_sanitized_status_for_heartbeat(tmp_path: Path) -> None:
    secrets = LocalSecretStore(
        key_file=tmp_path / "master.key",
        secrets_file=tmp_path / "secrets.json",
    )
    validations = LocalValidationStore(validations_file=tmp_path / "validations.json")
    secrets.put(provider="tbank", name="main", secret={"token": "tbank-secret-token"})
    validations.put(
        {
            "checkedAt": "2026-05-10T10:00:00Z",
            "name": "main",
            "permissions": {"accountReadable": True, "tradingAllowed": False},
            "provider": "tbank",
            "status": "passed",
            "warnings": ["no_full_access_accounts"],
        }
    )

    assert secrets.heartbeat_capability(validation_summaries=validations.list_public()) == {
        "brokerCredentialValidations": [
            {
                "checkedAt": "2026-05-10T10:00:00Z",
                "name": "main",
                "permissions": {"accountReadable": True, "tradingAllowed": False},
                "provider": "tbank",
                "status": "passed",
                "warnings": ["no_full_access_accounts"],
            }
        ],
        "localCredentials": [{"name": "main", "provider": "tbank"}],
    }
    assert "tbank-secret-token" not in (tmp_path / "validations.json").read_text(encoding="utf-8")


def test_validation_store_rejects_secret_like_fields(tmp_path: Path) -> None:
    validations = LocalValidationStore(validations_file=tmp_path / "validations.json")

    with pytest.raises(SecretFieldError):
        validations.put(
            {
                "name": "main",
                "provider": "tbank",
                "status": "failed",
                "token": "must-not-store",
            }
        )
