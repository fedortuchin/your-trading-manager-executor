from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ytm_executor.preflight as preflight_module
from ytm_executor.adapters import BrokerAdapterResult
from ytm_executor.binance_futures import BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER
from ytm_executor.okx_swap import OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER
from ytm_executor.preflight import preflight_command
from ytm_executor.risk import RiskPolicy, RiskState
from ytm_executor.secret_store import CredentialSummary


def test_preflight_acknowledges_external_paper_without_order_placement() -> None:
    decision = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
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
        "adapterPreflight": "passed",
        "adapterResult": {
            "adapter": "disabled",
            "clientOrderId": "ytm_command_1",
            "executorAction": "order_placement_skipped",
            "orderRequest": {
                "clientOrderId": "ytm_command_1",
                "executionMode": "external_paper",
                "limitPrice": "100",
                "leverage": "1",
                "marginMode": "cross",
                "market": "usdm_futures",
                "notional": "100",
                "orderType": "limit",
                "positionEffect": "open",
                "provider": "binance",
                "side": "long",
                "symbol": "BTCUSDT",
            },
            "reason": "broker adapters are not enabled in this foundation build",
        },
        "executorAction": "order_placement_skipped",
        "preflight": "passed",
        "provider": "binance",
        "reason": "broker adapters are not enabled in this foundation build",
        "riskPreflight": "passed",
        "zeroSecret": True,
    }


def test_preflight_rejects_command_secret_like_fields() -> None:
    command = _leased_command(provider="binance", execution_mode="external_paper")
    command["command"]["commandPayload"] = {"apiSecret": "must-not-execute"}

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
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
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
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
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
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
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
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
        risk_policy=_risk_policy(paper_only=False),
        risk_state=_risk_state(),
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


def test_preflight_rejects_missing_risk_policy() -> None:
    decision = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
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
    assert decision.result_payload["preflightReasonCode"] == "risk_policy_missing"
    assert decision.result_payload["riskPreflight"] == "failed"


def test_preflight_rejects_risk_kill_switch() -> None:
    decision = preflight_command(
        _leased_command(provider="binance", execution_mode="external_paper"),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(kill_switch=True),
        risk_state=_risk_state(),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "risk_kill_switch_enabled"


def test_preflight_rejects_order_over_local_notional_limit() -> None:
    command = _leased_command(provider="binance", execution_mode="external_paper")
    command["command"]["commandPayload"]["orderNotional"] = "1001"

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "risk_order_notional_exceeded"


def test_preflight_rejects_invalid_adapter_order_request() -> None:
    command = _leased_command(provider="binance", execution_mode="external_paper")
    command["command"].pop("side")

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "adapter_order_request_invalid"
    assert decision.result_payload["riskPreflight"] == "passed"


def test_preflight_binance_futures_adapter_requires_real_mode() -> None:
    command = _leased_command(provider="binance", execution_mode="external_paper")
    command["command"]["commandPayload"]["adapter"] = (
        BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER
    )

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(),
        risk_state=_risk_state(),
        validation_summaries=(
            _validation(provider="binance", checked_at="2026-05-10T10:00:00Z"),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "adapter_preflight_failed"


def test_preflight_binance_futures_adapter_requires_local_secret() -> None:
    command = _leased_command(provider="binance", execution_mode="real")
    command["command"]["commandPayload"]["adapter"] = (
        BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER
    )

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        risk_policy=_risk_policy(paper_only=False),
        risk_state=_risk_state(),
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
    assert decision.result_payload["preflightReasonCode"] == "adapter_preflight_failed"


def test_preflight_binance_futures_validate_only_then_rejects_real_placement(
    monkeypatch,
) -> None:
    command = _leased_command(provider="binance", execution_mode="real")
    command["command"]["commandPayload"]["adapter"] = (
        BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER
    )
    command["command"]["commandPayload"]["quantity"] = "0.01"
    seen: dict[str, Any] = {}

    class FakeBinanceFuturesAdapter:
        def __init__(self, *, api_key: str, api_secret: str) -> None:
            seen["api_key"] = api_key
            seen["api_secret"] = api_secret

        def prepare_order(self, request):
            seen["request"] = request
            return BrokerAdapterResult(
                status="acknowledged",
                payload={
                    "adapter": BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
                    "clientOrderId": request.client_order_id,
                    "executorAction": "order_test_validated",
                    "mainnet": True,
                    "market": "usdm_futures",
                    "provider": "binance",
                },
            )

    monkeypatch.setattr(
        preflight_module,
        "BinanceUsdmFuturesMainnetOrderTestAdapter",
        FakeBinanceFuturesAdapter,
    )

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        local_secret_resolver=lambda provider, name: {
            "apiKey": f"{provider}-{name}-public",
            "apiSecret": "binance-private-secret",
        },
        risk_policy=_risk_policy(paper_only=False),
        risk_state=_risk_state(),
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
    assert decision.result_payload["adapterPreflight"] == "passed"
    assert decision.result_payload["adapterResult"]["executorAction"] == "order_test_validated"
    assert seen["api_secret"] == "binance-private-secret"
    assert "binance-private-secret" not in repr(decision.result_payload)


def test_preflight_okx_swap_precheck_then_rejects_real_placement(monkeypatch) -> None:
    command = _leased_command(provider="okx", execution_mode="real")
    command["command"]["symbol"] = "BTC-USDT-SWAP"
    command["command"]["commandPayload"]["adapter"] = OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER
    command["command"]["commandPayload"]["market"] = "okx_swap"
    command["command"]["commandPayload"]["quantity"] = "1"
    seen: dict[str, Any] = {}

    class FakeOkxSwapAdapter:
        def __init__(self, *, api_key: str, api_secret: str, passphrase: str) -> None:
            seen["api_key"] = api_key
            seen["api_secret"] = api_secret
            seen["passphrase"] = passphrase

        def prepare_order(self, request):
            seen["request"] = request
            return BrokerAdapterResult(
                status="acknowledged",
                payload={
                    "adapter": OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
                    "clientOrderId": "ytm_okx_1",
                    "executorAction": "order_precheck_validated",
                    "mainnet": True,
                    "market": "okx_swap",
                    "provider": "okx",
                },
            )

    monkeypatch.setattr(preflight_module, "OkxSwapMainnetOrderPrecheckAdapter", FakeOkxSwapAdapter)

    decision = preflight_command(
        command,
        local_credentials=(CredentialSummary(provider="okx", name="main"),),
        local_secret_resolver=lambda provider, name: {
            "apiKey": f"{provider}-{name}-public",
            "apiSecret": "okx-private-secret",
            "passphrase": "okx-passphrase",
        },
        risk_policy=_risk_policy(paper_only=False, market="okx_swap", symbol="BTC-USDT-SWAP"),
        risk_state=_risk_state(),
        validation_summaries=(
            _validation(
                provider="okx",
                checked_at="2026-05-10T10:00:00Z",
                trading_allowed=False,
            ),
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "real_execution_disabled"
    assert decision.result_payload["adapterPreflight"] == "passed"
    assert decision.result_payload["adapterResult"]["executorAction"] == "order_precheck_validated"
    assert seen["api_secret"] == "okx-private-secret"
    assert "okx-private-secret" not in repr(decision.result_payload)
    assert "okx-passphrase" not in repr(decision.result_payload)


def _leased_command(*, provider: str, execution_mode: str) -> dict[str, object]:
    return {
        "command": {
            "clientOrderId": "ytm_command_1",
            "commandPayload": {
                "leverage": "1",
                "marginMode": "cross",
                "market": "usdm_futures",
                "orderNotional": "100",
                "orderType": "limit",
                "positionEffect": "open",
                "price": "100",
                "projectedPositionNotional": "100",
            },
            "executionAccountSource": "provider",
            "executionMode": execution_mode,
            "id": "command-1",
            "provider": provider,
            "side": "long",
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


def _risk_policy(
    *,
    kill_switch: bool = False,
    paper_only: bool = True,
    market: str = "usdm_futures",
    symbol: str = "BTCUSDT",
) -> RiskPolicy:
    return RiskPolicy(
        configured=True,
        enabled=True,
        kill_switch=kill_switch,
        paper_only=paper_only,
        allowed_markets=(market,),
        allowed_margin_modes=("cross",),
        allowed_symbols=(symbol,),
        allowed_order_types=("limit",),
        max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("5000"),
        max_symbol_notional={symbol: Decimal("5000")},
        max_daily_loss=Decimal("250"),
        max_leverage=Decimal("1"),
        position_mode="one_way",
    )


def _risk_state() -> RiskState:
    return RiskState(realized_loss_by_date={})
