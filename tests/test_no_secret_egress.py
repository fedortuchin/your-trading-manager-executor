from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ytm_executor.client import YtmClient
from ytm_executor.guards import SecretFieldError
from ytm_executor.secret_store import LocalSecretStore


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
