"""YTM executor API client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from ytm_executor.guards import reject_secret_fields


class Transport(Protocol):
    def post(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
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
    ) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        api_request = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
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

    def lease_command(self, *, access_token: str) -> dict[str, Any]:
        return self._post(
            path="/api/executor/commands/lease",
            payload={},
            access_token=access_token,
        )

    def _post(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        access_token: str | None,
    ) -> dict[str, Any]:
        reject_secret_fields(payload)
        url = f"{self.server_url.rstrip('/')}{path}"
        host = urlparse(url).hostname
        if host not in self.allowed_hosts:
            raise RuntimeError(f"egress host is not allowed: {host}")
        headers = {"Content-Type": "application/json"}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        return self.transport.post(url=url, payload=payload, headers=headers)
