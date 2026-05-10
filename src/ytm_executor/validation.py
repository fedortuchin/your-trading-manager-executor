"""Validate broker credentials from the executor host without sending secrets to YTM."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib import parse, request
from urllib.error import HTTPError, URLError


class JsonGetTransport(Protocol):
    def get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class UrlLibJsonGetTransport:
    def get_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        api_request = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(api_request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise BrokerValidationError(_http_failure_reason(exc.code)) from exc
        except URLError as exc:
            raise BrokerValidationError("broker_request_failed") from exc
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise BrokerValidationError("broker_returned_non_object")
        return parsed


class BrokerValidationError(RuntimeError):
    """A broker validation request failed without exposing raw provider response text."""


def validate_broker_credential(
    *,
    provider: str,
    name: str,
    secret: dict[str, str],
    now: datetime | None = None,
    transport: JsonGetTransport | None = None,
) -> dict[str, Any]:
    normalized_provider = _required_text(provider, "provider")
    normalized_name = _required_text(name, "name")
    checked_at = _iso_z(now or datetime.now(UTC))
    if normalized_provider == "binance":
        return _validate_binance(
            name=normalized_name,
            secret=secret,
            checked_at=checked_at,
            transport=transport or UrlLibJsonGetTransport(),
        )
    if normalized_provider == "tbank":
        return _validate_tbank(name=normalized_name, secret=secret, checked_at=checked_at)
    return _summary(
        provider=normalized_provider,
        name=normalized_name,
        checked_at=checked_at,
        status="failed",
        failure_reason="unsupported_provider",
        permissions={"accountReadable": False, "tradingAllowed": False},
        warnings=["unsupported_provider"],
    )


def _validate_binance(
    *,
    name: str,
    secret: dict[str, str],
    checked_at: str,
    transport: JsonGetTransport,
) -> dict[str, Any]:
    public_id = _required_text(secret.get("apiKey"), "api_key")
    private_material = _required_text(secret.get("apiSecret"), "api_secret")
    timestamp_ms = str(int(time.time() * 1000))
    query = parse.urlencode({"recvWindow": "5000", "timestamp": timestamp_ms})
    signature = hmac.new(
        private_material.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"https://fapi.binance.com/fapi/v3/account?{query}&signature={signature}"
    try:
        payload = transport.get_json(
            url=url,
            headers={"X-MBX-APIKEY": public_id},
            timeout_seconds=15,
        )
    except BrokerValidationError as exc:
        return _failed_provider_summary(
            provider="binance",
            name=name,
            checked_at=checked_at,
            failure_reason=str(exc) or "broker_request_failed",
        )

    can_trade = payload.get("canTrade") is True
    can_withdraw = payload.get("canWithdraw") is True
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    positions = payload.get("positions")
    if not isinstance(positions, list):
        positions = []
    account_type = "USDS_M_FUTURES"
    fingerprint_parts = [
        account_type,
        str(payload.get("feeTier") or ""),
        str(payload.get("multiAssetsMargin") or ""),
        str(len(assets)),
        str(len(positions)),
        str(can_trade),
    ]
    warnings = []
    if not can_trade:
        warnings.append("trading_not_allowed")
    if can_withdraw:
        warnings.append("withdrawals_allowed")
    return _summary(
        provider="binance",
        name=name,
        checked_at=checked_at,
        status="passed",
        account_fingerprint=_fingerprint("binance", fingerprint_parts),
        permissions={
            "accountReadable": True,
            "tradingAllowed": can_trade,
            "withdrawalsAllowed": can_withdraw,
            "brokerAccountType": account_type,
            "market": "usdm_futures",
            "assetCount": len(assets),
            "positionCount": len(positions),
        },
        warnings=warnings,
    )


def _validate_tbank(*, name: str, secret: dict[str, str], checked_at: str) -> dict[str, Any]:
    broker_access = _required_text(secret.get("token"), "token")
    try:
        from t_tech.invest import AccessLevel, Client
    except ImportError:
        return _failed_provider_summary(
            provider="tbank",
            name=name,
            checked_at=checked_at,
            failure_reason="adapter_dependency_missing",
        )

    try:
        with Client(broker_access) as client:
            response = client.users.get_accounts()
    except Exception:
        return _failed_provider_summary(
            provider="tbank",
            name=name,
            checked_at=checked_at,
            failure_reason="broker_request_failed",
        )

    accounts = list(getattr(response, "accounts", ()) or ())
    full_access_count = 0
    read_only_count = 0
    no_access_count = 0
    fingerprint_parts = []
    for account in accounts:
        level = getattr(account, "access_level", None)
        account_id = str(getattr(account, "id", ""))
        status = str(getattr(account, "status", ""))
        account_type = str(getattr(account, "type", ""))
        fingerprint_parts.append(f"{account_id}:{status}:{account_type}:{level}")
        if level == AccessLevel.ACCOUNT_ACCESS_LEVEL_FULL_ACCESS:
            full_access_count += 1
        elif level == AccessLevel.ACCOUNT_ACCESS_LEVEL_READ_ONLY:
            read_only_count += 1
        elif level == AccessLevel.ACCOUNT_ACCESS_LEVEL_NO_ACCESS:
            no_access_count += 1
    warnings = []
    if not accounts:
        warnings.append("no_accounts_visible")
    if full_access_count == 0:
        warnings.append("no_full_access_accounts")
    return _summary(
        provider="tbank",
        name=name,
        checked_at=checked_at,
        status="passed" if accounts else "failed",
        failure_reason=None if accounts else "no_accounts_visible",
        account_fingerprint=_fingerprint("tbank", sorted(fingerprint_parts)) if accounts else None,
        permissions={
            "accountReadable": bool(accounts),
            "tradingAllowed": full_access_count > 0,
            "accountCount": len(accounts),
            "fullAccessAccountCount": full_access_count,
            "readOnlyAccountCount": read_only_count,
            "noAccessAccountCount": no_access_count,
        },
        warnings=warnings,
    )


def _failed_provider_summary(
    *,
    provider: str,
    name: str,
    checked_at: str,
    failure_reason: str,
) -> dict[str, Any]:
    return _summary(
        provider=provider,
        name=name,
        checked_at=checked_at,
        status="failed",
        failure_reason=failure_reason,
        permissions={"accountReadable": False, "tradingAllowed": False},
        warnings=[failure_reason],
    )


def _summary(
    *,
    provider: str,
    name: str,
    checked_at: str,
    status: str,
    permissions: dict[str, Any],
    warnings: list[str],
    account_fingerprint: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "checkedAt": checked_at,
        "name": name,
        "permissions": permissions,
        "provider": provider,
        "status": status,
        "warnings": warnings,
    }
    if account_fingerprint:
        result["accountFingerprint"] = account_fingerprint
    if failure_reason:
        result["failureReason"] = failure_reason
    return result


def _fingerprint(provider: str, parts: list[str]) -> str:
    raw = "|".join(parts).encode("utf-8")
    return f"{provider}:{hashlib.sha256(raw).hexdigest()[:16]}"


def _http_failure_reason(status_code: int) -> str:
    if status_code in {400, 401, 403}:
        return "credential_rejected"
    if status_code == 429:
        return "broker_rate_limited"
    return "broker_request_failed"


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text
