"""YTM executor API client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse

from ytm_executor.guards import reject_secret_fields


class Transport(Protocol):
    def post(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HttpTransport:
    timeout_seconds: int = 15

    def post(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        api_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
            with request.urlopen(api_request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(f"YTM request failed: {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"YTM request failed: {exc}") from exc
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("YTM returned non-object JSON")
        return parsed


@dataclass(frozen=True, slots=True)
class YtmClient:
    server_url: str
    allowed_hosts: tuple[str, ...]
    transport: Transport = HttpTransport()

    def enroll(
        self,
        *,
        enrollment_token: str,
        client_version: str,
        capabilities: dict[str, Any],
        allowed_egress: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            path="/api/executor/enroll",
            payload={
                "allowedEgress": allowed_egress,
                "capabilities": capabilities,
                "clientVersion": client_version,
                "enrollmentToken": enrollment_token,
            },
            access_token=None,
        )

    def heartbeat(
        self,
        *,
        access_token: str,
        client_version: str,
        capabilities: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            path="/api/executor/heartbeat",
            payload={
                "capabilities": capabilities,
                "clientVersion": client_version,
                "heartbeatStatus": "online",
            },
            access_token=access_token,
        )

    def lease_command(
        self,
        *,
        access_token: str,
        wait_seconds: float = 0.0,
        poll_interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        query = urlencode(
            {
                "pollIntervalSeconds": poll_interval_seconds,
                "waitSeconds": wait_seconds,
            }
        )
        return self._post(
            path=f"/api/executor/commands/lease?{query}",
            payload={},
            access_token=access_token,
            timeout_seconds=max(15.0, float(wait_seconds) + 10.0),
        )

    def record_command_result(
        self,
        *,
        access_token: str,
        command_id: str,
        lease_id: str,
        status: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            path=f"/api/executor/commands/{command_id}/result",
            payload={
                "leaseId": lease_id,
                "resultPayload": result_payload,
                "status": status,
            },
            access_token=access_token,
        )

    def record_reconciliation_snapshot(
        self,
        *,
        access_token: str,
        snapshot_type: str,
        status: str,
        payload: dict[str, Any],
        execution_mode: str | None = None,
        provider_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        request_payload: dict[str, Any] = {
            "payload": payload,
            "snapshotType": snapshot_type,
            "status": status,
        }
        if execution_mode is not None:
            request_payload["executionMode"] = execution_mode
        if provider_snapshot_id is not None:
            request_payload["providerSnapshotId"] = provider_snapshot_id
        return self._post(
            path="/api/executor/reconciliation/snapshots",
            payload=request_payload,
            access_token=access_token,
        )

    def _post(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        access_token: str | None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        reject_secret_fields(payload)
        url = f"{self.server_url.rstrip('/')}{path}"
        host = urlparse(url).hostname
        if host not in self.allowed_hosts:
            raise RuntimeError(f"egress host is not allowed: {host}")
        headers = {"Content-Type": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        return self.transport.post(
            url=url,
            payload=payload,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
