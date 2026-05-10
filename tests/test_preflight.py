from __future__ import annotations

from datetime import UTC, datetime

from ytm_executor.preflight import preflight_command
from ytm_executor.secret_store import CredentialSummary


def test_preflight_acknowledges_external_paper_without_order_placement() -> None:
    decision = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(
                provider="binance",
                checked_at="2026-05-10T10:00:00Z",
                trading_allowed=False,
            ),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "acknowledged"
    assert decision.result_payload == {
        "executionMode": "external_paper",
        "executorAction": "order_placement_skipped",
        "preflight": "passed",
        "provider": "binance",
        "reason": "broker adapters are not enabled in this foundation build",
        "zeroSecret": True,
    }


def test_preflight_rejects_command_secret_like_fields() -> None:
    command = _leased_command(provider="binance", execution_mode="external_paper")
    command["command"]["commandPayload"] = {"apiSecret": "must-not-execute"}

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["executorAction"] == "local_preflight_failed"
    assert decision.result_payload["preflightReasonCode"] == "command_contains_secret_fields"
    assert "must-not-execute" not in repr(decision.result_payload)


def test_preflight_rejects_missing_local_credential() -> None:
    decision = preflight_command(
        _leased_command(provider="tbank", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(provider="tbank", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "local_credential_missing"


def test_preflight_rejects_failed_or_stale_validation() -> None:
    failed = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(
                provider="binance",
                checked_at="2026-05-10T10:00:00Z",
                status="failed",
            ),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )
    stale = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-09T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert failed.status == "rejected"
    assert failed.result_payload["preflightReasonCode"] == "credential_validation_failed"
    assert stale.status == "rejected"
    assert stale.result_payload["preflightReasonCode"] == "credential_validation_stale"


def test_preflight_rejects_real_execution_even_with_valid_credential() -> None:
    decision = preflight_command(
        _leased_command(provider="binance", execution_mode="real"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        validation_summaries=(
            _validation(
                provider="binance",
                checked_at="2026-05-10T10:00:00Z",
                trading_allowed=True,
            ),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "real_execution_disabled"


def _leased_command(*, provider: str, execution_mode: str) -> dict[str, object]:
    return {
        "command": {
            "commandPayload": {},
            "executionAccountSource": "provider",
            "executionMode": execution_mode,
            "id": "command-1",
            "provider": provider,
            "status": "created",
            "symbol": "BTCUSDT",
        },
        "lease": {"id": "lease-1"},
    }


def _validation(
    *,
    provider: str,
    checked_at: str,
    status: str = "passed",
    trading_allowed: bool = False,
) -> dict[str, object]:
    return {
        "checkedAt": checked_at,
        "name": "main",
        "permissions": {
            "accountReadable": True,
            "tradingAllowed": trading_allowed,
            "withdrawalsAllowed": False,
        },
        "provider": provider,
        "status": status,
        "warnings": [],
    }
