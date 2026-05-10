from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ytm_executor.client import YtmClient
from ytm_executor.guards import SecretFieldError
from ytm_executor.secret_store import LocalSecretStore
from ytm_executor.validation import validate_broker_credential
from ytm_executor.validation_store import LocalValidationStore


@dataclass(slots=True)
class CaptureTransport:
    responses: list[dict[str, Any]]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.requests.append({"headers": headers, "payload": payload, "url": url})
        return self.responses.pop(0)


@dataclass(slots=True)
class BrokerCaptureTransport:
    responses: list[dict[str, Any]]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.requests.append(
            {"headers": headers, "timeout_seconds": timeout_seconds, "url": url}
        )
        return self.responses.pop(0)


def test_local_broker_token_is_not_sent_to_ytm(tmp_path: Path) -> None:
    store = LocalSecretStore(
        key_file=tmp_path / "master.key",
        secrets_file=tmp_path / "secrets.json",
    )
    store.put(
        provider="tbank",
        name="main",
        secret={"token": "tbank-secret-token"},
    )
    transport = CaptureTransport(
        responses=[
            {
                "executor": {
                    "id": "executor-1",
                },
                "accessToken": "ytm_exec_access",
            },
            {"executor": {"id": "executor-1"}},
            {"item": None},
        ]
    )
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=transport,
    )

    enrolled = client.enroll(
        allowed_egress={"ytmApi": "ytm.example.test"},
        capabilities={"leases": True, "zeroSecret": True},
        client_version="test",
        enrollment_token="ytm_enroll_token",
    )
    capabilities = {"leases": True, "zeroSecret": True}
    capabilities.update(store.heartbeat_capability())
    client.heartbeat(
        access_token=enrolled["accessToken"],
        capabilities=capabilities,
        client_version="test",
    )
    client.lease_command(access_token=enrolled["accessToken"])

    raw_requests = repr(transport.requests)
    assert "tbank-secret-token" not in raw_requests
    assert "localCredentials" in raw_requests
    assert "main" in raw_requests


def test_validated_broker_secret_is_not_sent_to_ytm(tmp_path: Path) -> None:
    store = LocalSecretStore(
        key_file=tmp_path / "master.key",
        secrets_file=tmp_path / "secrets.json",
    )
    validations = LocalValidationStore(validations_file=tmp_path / "validations.json")
    store.put(
        provider="binance",
        name="main",
        secret={"apiKey": "binance-public-key", "apiSecret": "binance-private-secret"},
    )
    broker_transport = BrokerCaptureTransport(
        responses=[
            {
                "assets": [{"asset": "USDT"}],
                "canTrade": True,
                "canWithdraw": False,
                "feeTier": 0,
                "multiAssetsMargin": False,
                "positions": [{"symbol": "BTCUSDT"}],
            }
        ]
    )
    summary = validate_broker_credential(
        provider="binance",
        name="main",
        secret=store.get(provider="binance", name="main"),
        transport=broker_transport,
    )
    validations.put(summary)
    ytm_transport = CaptureTransport(
        responses=[
            {"executor": {"id": "executor-1"}, "accessToken": "ytm_exec_access"},
            {"executor": {"id": "executor-1"}},
        ]
    )
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=ytm_transport,
    )

    enrolled = client.enroll(
        allowed_egress={"ytmApi": "ytm.example.test"},
        capabilities={"leases": True, "zeroSecret": True},
        client_version="test",
        enrollment_token="ytm_enroll_token",
    )
    capabilities = {"leases": True, "zeroSecret": True}
    capabilities.update(store.heartbeat_capability(validation_summaries=validations.list_public()))
    client.heartbeat(
        access_token=enrolled["accessToken"],
        capabilities=capabilities,
        client_version="test",
    )

    broker_requests = repr(broker_transport.requests)
    assert "binance-public-key" in broker_requests
    assert "binance-private-secret" not in broker_requests
    raw_ytm_requests = repr(ytm_transport.requests)
    assert "binance-public-key" not in raw_ytm_requests
    assert "binance-private-secret" not in raw_ytm_requests
    assert "brokerCredentialValidations" in raw_ytm_requests
    assert "accountFingerprint" in raw_ytm_requests


def test_client_rejects_secret_like_payload_keys() -> None:
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=CaptureTransport(responses=[]),
    )

    with pytest.raises(SecretFieldError):
        client.heartbeat(
            access_token="ytm_exec_access",
            capabilities={"apiSecret": "must-not-send"},
            client_version="test",
        )


def test_client_records_sanitized_command_result() -> None:
    transport = CaptureTransport(
        responses=[{"command": {"id": "command-1", "status": "acknowledged"}}]
    )
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=transport,
    )

    response = client.record_command_result(
        access_token="ytm_exec_access",
        command_id="command-1",
        lease_id="lease-1",
        status="acknowledged",
        result_payload={"executorAction": "order_placement_skipped"},
    )

    assert response["command"]["status"] == "acknowledged"
    assert transport.requests[0]["payload"]["resultPayload"] == {
        "executorAction": "order_placement_skipped"
    }


def test_client_rejects_secret_like_command_result() -> None:
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=CaptureTransport(responses=[]),
    )

    with pytest.raises(SecretFieldError):
        client.record_command_result(
            access_token="ytm_exec_access",
            command_id="command-1",
            lease_id="lease-1",
            status="acknowledged",
            result_payload={"token": "must-not-send"},
        )


def test_client_records_sanitized_reconciliation_snapshot() -> None:
    transport = CaptureTransport(
        responses=[{"snapshot": {"id": "snapshot-1", "status": "ok"}}]
    )
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=transport,
    )

    response = client.record_reconciliation_snapshot(
        access_token="ytm_exec_access",
        execution_mode="external_paper",
        payload={"orders": [], "positions": []},
        snapshot_type="full",
        status="ok",
    )

    assert response["snapshot"]["status"] == "ok"
    assert transport.requests[0]["payload"]["payload"] == {"orders": [], "positions": []}


def test_client_rejects_secret_like_reconciliation_snapshot() -> None:
    client = YtmClient(
        allowed_hosts=("ytm.example.test",),
        server_url="https://ytm.example.test",
        transport=CaptureTransport(responses=[]),
    )

    with pytest.raises(SecretFieldError):
        client.record_reconciliation_snapshot(
            access_token="ytm_exec_access",
            payload={"orders": [{"apiSecret": "must-not-send"}]},
            snapshot_type="full",
            status="ok",
        )
