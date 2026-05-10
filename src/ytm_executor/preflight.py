"""Local command preflight before any broker adapter can run."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ytm_executor.guards import SecretFieldError, reject_secret_fields
from ytm_executor.risk import (
    RiskPolicy,
    RiskState,
    evaluate_command_risk,
    missing_risk_policy,
)
from ytm_executor.secret_store import CredentialSummary

VALIDATION_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class CommandPreflightDecision:
    status: str
    result_payload: dict[str, Any]


def preflight_command(
    item: dict[str, Any],
    *,
    local_credentials: Iterable[CredentialSummary],
    risk_policy: RiskPolicy | None = None,
    risk_state: RiskState | None = None,
    validation_summaries: Iterable[dict[str, Any]],
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
    if execution_mode == "real":
        return _reject(
            "real_execution_disabled",
            "real execution is disabled in this executor build",
        )
    return _acknowledge_without_order_placement(provider=provider, execution_mode=execution_mode)


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
    provider: str,
    execution_mode: str,
) -> CommandPreflightDecision:
    return _decision(
        status="acknowledged",
        result_payload={
            "executionMode": execution_mode,
            "executorAction": "order_placement_skipped",
            "preflight": "passed",
            "provider": provider,
            "reason": "broker adapters are not enabled in this foundation build",
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
