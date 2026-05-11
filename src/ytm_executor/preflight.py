"""Local command preflight before any broker adapter can run."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ytm_executor.adapters import DisabledBrokerAdapter, build_order_request
from ytm_executor.binance_futures import (
    BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
    BinanceUsdmFuturesMainnetOrderTestAdapter,
)
from ytm_executor.guards import SecretFieldError, reject_secret_fields
from ytm_executor.okx_swap import (
    OKX_SWAP_MAINNET_ORDER_ADAPTER,
    OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
    OkxSwapMainnetOrderPlacementAdapter,
    OkxSwapMainnetOrderPrecheckAdapter,
)
from ytm_executor.risk import (
    RiskPolicy,
    RiskState,
    evaluate_command_risk,
    missing_risk_policy,
)
from ytm_executor.secret_store import CredentialSummary

VALIDATION_TTL_SECONDS = 24 * 60 * 60
VALIDATE_ONLY_REAL_ADAPTERS = frozenset(
    {
        BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
        OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
    }
)
REAL_ORDER_ADAPTERS = frozenset({OKX_SWAP_MAINNET_ORDER_ADAPTER})


@dataclass(frozen=True, slots=True)
class CommandPreflightDecision:
    status: str
    result_payload: dict[str, Any]


def preflight_command(
    item: dict[str, Any],
    *,
    local_credentials: Iterable[CredentialSummary],
    local_secret_resolver: Callable[[str, str], dict[str, str]] | None = None,
    risk_policy: RiskPolicy | None = None,
    risk_state: RiskState | None = None,
    validation_summaries: Iterable[dict[str, Any]],
    real_order_placement_enabled: bool = False,
    now: datetime | None = None,
) -> CommandPreflightDecision:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    command = item.get("command")
    if not isinstance(command, dict):
        return _reject("command_missing", "leased item does not contain a command")
    try:
        reject_secret_fields(command)
    except SecretFieldError:
        return _reject("command_contains_secret_fields", "command contains secret-like fields")

    provider = _text(command.get("provider"))
    if not provider:
        return _reject("provider_missing", "command provider is missing")
    execution_mode = _text(command.get("executionMode"))
    if execution_mode not in {"external_paper", "real"}:
        return _reject("unsupported_execution_mode", "execution mode is not provider-backed")
    if _text(command.get("executionAccountSource")) != "provider":
        return _reject(
            "execution_account_source_mismatch",
            "execution account source is not provider",
        )
    if provider not in {credential.provider for credential in local_credentials}:
        return _reject("local_credential_missing", "local broker credential is not configured")

    validation_block = _validation_block(
        provider=provider,
        validation_summaries=validation_summaries,
        now=timestamp,
    )
    if validation_block is not None:
        return validation_block
    risk_decision = evaluate_command_risk(
        command,
        execution_mode=execution_mode,
        policy=risk_policy or missing_risk_policy(),
        state=risk_state or RiskState(realized_loss_by_date={}),
        now=timestamp,
    )
    if not risk_decision.passed:
        return _reject(
            risk_decision.reason_code or "risk_preflight_failed",
            risk_decision.reason or "local risk preflight failed",
            extra={"riskPreflight": "failed"},
        )
    try:
        order_request = build_order_request(command, execution_mode=execution_mode)
    except ValueError as exc:
        return _reject(
            "adapter_order_request_invalid",
            str(exc),
            extra={"adapterPreflight": "failed", "riskPreflight": "passed"},
        )
    adapter_name = _adapter_name(command)
    if execution_mode == "real" and adapter_name not in (
        VALIDATE_ONLY_REAL_ADAPTERS | REAL_ORDER_ADAPTERS
    ):
        return _reject(
            "real_execution_disabled",
            "real execution is disabled in this executor build",
            extra={"adapterPreflight": "blocked", "riskPreflight": "passed"},
        )
    if (
        execution_mode == "real"
        and adapter_name in REAL_ORDER_ADAPTERS
        and not real_order_placement_enabled
    ):
        return _reject(
            "real_execution_disabled",
            "real order placement requires explicit local executor enablement",
            extra={"adapterPreflight": "blocked", "riskPreflight": "passed"},
        )
    try:
        adapter = _adapter_for_command(
            command,
            execution_mode=execution_mode,
            provider=provider,
            local_secret_resolver=local_secret_resolver,
        )
        adapter_result = adapter.prepare_order(order_request)
    except ValueError as exc:
        return _reject(
            "adapter_preflight_failed",
            str(exc),
            extra={"adapterPreflight": "failed", "riskPreflight": "passed"},
        )
    if execution_mode == "real" and adapter_name in VALIDATE_ONLY_REAL_ADAPTERS:
        return _reject(
            "real_execution_disabled",
            "real order placement is disabled in this executor build after validate-only preflight",
            extra={
                "adapterPreflight": "passed",
                "adapterResult": adapter_result.payload,
                "riskPreflight": "passed",
            },
        )
    if execution_mode == "real":
        return _acknowledge_real_order_placement(
            adapter_payload=adapter_result.payload,
            provider=provider,
        )
    return _acknowledge_without_order_placement(
        adapter_payload=adapter_result.payload,
        provider=provider,
        execution_mode=execution_mode,
    )


def _acknowledge_real_order_placement(
    *,
    adapter_payload: dict[str, Any],
    provider: str,
) -> CommandPreflightDecision:
    result_payload = {
        "adapterPreflight": "passed",
        "adapterResult": adapter_payload,
        "executionMode": "real",
        "executorAction": str(adapter_payload.get("executorAction", "order_submitted")),
        "preflight": "passed",
        "provider": provider,
        "riskPreflight": "passed",
        "zeroSecret": True,
    }
    for key in ("clientOrderId", "providerOrderId", "providerStatus"):
        value = adapter_payload.get(key)
        if isinstance(value, str) and value:
            result_payload[key] = value
    return _decision(status="acknowledged", result_payload=result_payload)


def _validation_block(
    *,
    provider: str,
    validation_summaries: Iterable[dict[str, Any]],
    now: datetime,
) -> CommandPreflightDecision | None:
    matching = [
        dict(summary)
        for summary in validation_summaries
        if isinstance(summary, dict) and _text(summary.get("provider")) == provider
    ]
    if not matching:
        return _reject("credential_validation_required", "local broker credential is not validated")
    validation = max(
        matching,
        key=lambda summary: _checked_at(summary) or datetime.min.replace(tzinfo=UTC),
    )
    if _text(validation.get("status")) != "passed":
        return _reject("credential_validation_failed", "local broker credential validation failed")
    checked_at = _checked_at(validation)
    if checked_at is None:
        return _reject(
            "credential_validation_invalid",
            "local broker credential validation is invalid",
        )
    age_seconds = (now - checked_at).total_seconds()
    if age_seconds < -300:
        return _reject(
            "credential_validation_invalid",
            "local broker credential validation is invalid",
        )
    if age_seconds > VALIDATION_TTL_SECONDS:
        return _reject("credential_validation_stale", "local broker credential validation is stale")
    permissions = validation.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("accountReadable") is not True:
        return _reject(
            "credential_validation_account_not_readable",
            "local broker credential validation did not confirm account access",
        )
    return None


def _acknowledge_without_order_placement(
    *,
    adapter_payload: dict[str, Any],
    provider: str,
    execution_mode: str,
) -> CommandPreflightDecision:
    return _decision(
        status="acknowledged",
        result_payload={
            "executionMode": execution_mode,
            "executorAction": "order_placement_skipped",
            "adapterPreflight": "passed",
            "adapterResult": adapter_payload,
            "preflight": "passed",
            "provider": provider,
            "reason": str(
                adapter_payload.get(
                    "reason",
                    "broker adapters are not enabled in this foundation build",
                )
            ),
            "riskPreflight": "passed",
            "zeroSecret": True,
        },
    )


def _reject(
    reason_code: str,
    reason: str,
    *,
    extra: dict[str, Any] | None = None,
) -> CommandPreflightDecision:
    result_payload = {
        "executorAction": "local_preflight_failed",
        "preflight": "failed",
        "preflightReasonCode": reason_code,
        "reason": reason,
        "zeroSecret": True,
    }
    if extra:
        result_payload.update(extra)
    return _decision(
        status="rejected",
        result_payload=result_payload,
    )


def _decision(*, status: str, result_payload: dict[str, Any]) -> CommandPreflightDecision:
    reject_secret_fields(result_payload)
    return CommandPreflightDecision(status=status, result_payload=result_payload)


def _adapter_for_command(
    command: dict[str, Any],
    *,
    execution_mode: str,
    provider: str,
    local_secret_resolver: Callable[[str, str], dict[str, str]] | None,
):
    adapter_name = _adapter_name(command)
    if adapter_name in {OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER, OKX_SWAP_MAINNET_ORDER_ADAPTER}:
        return _okx_adapter_for_command(
            command,
            adapter_name=adapter_name,
            execution_mode=execution_mode,
            provider=provider,
            local_secret_resolver=local_secret_resolver,
        )
    if adapter_name != BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER:
        return DisabledBrokerAdapter(provider=provider)
    if provider != "binance":
        raise ValueError("Binance Futures mainnet adapter requires provider=binance")
    if execution_mode != "real":
        raise ValueError("Binance Futures mainnet order test requires executionMode=real")
    if local_secret_resolver is None:
        raise ValueError("local Binance credential is required for Futures mainnet adapter")
    credential_name = _text(command.get("credentialName")) or "main"
    secret = local_secret_resolver(provider, credential_name)
    api_key = _text(secret.get("apiKey"))
    api_secret = _text(secret.get("apiSecret"))
    if not api_key or not api_secret:
        raise ValueError(
            "local Binance API key and secret are required for Futures mainnet adapter"
        )
    return BinanceUsdmFuturesMainnetOrderTestAdapter(api_key=api_key, api_secret=api_secret)


def _okx_adapter_for_command(
    command: dict[str, Any],
    *,
    adapter_name: str,
    execution_mode: str,
    provider: str,
    local_secret_resolver: Callable[[str, str], dict[str, str]] | None,
):
    if provider != "okx":
        raise ValueError("OKX SWAP mainnet adapter requires provider=okx")
    if execution_mode not in {"external_paper", "real"}:
        raise ValueError("OKX SWAP order precheck requires provider-backed execution")
    if local_secret_resolver is None:
        raise ValueError("local OKX credential is required for SWAP mainnet adapter")
    credential_name = _text(command.get("credentialName")) or "main"
    secret = local_secret_resolver(provider, credential_name)
    api_key = _text(secret.get("apiKey"))
    api_secret = _text(secret.get("apiSecret"))
    passphrase = _text(secret.get("passphrase"))
    if not api_key or not api_secret or not passphrase:
        raise ValueError("local OKX API key, secret, and passphrase are required")
    if adapter_name == OKX_SWAP_MAINNET_ORDER_ADAPTER:
        if execution_mode != "real":
            raise ValueError("OKX SWAP order placement requires executionMode=real")
        return OkxSwapMainnetOrderPlacementAdapter(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
    return OkxSwapMainnetOrderPrecheckAdapter(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
    )


def _adapter_name(command: dict[str, Any]) -> str:
    payload = command.get("commandPayload")
    if isinstance(payload, dict):
        value = payload.get("adapter") or payload.get("adapterMode")
        if isinstance(value, str):
            return value.strip()
    return ""


def _checked_at(summary: dict[str, Any]) -> datetime | None:
    value = summary.get("checkedAt")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _text(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""
