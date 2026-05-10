from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import ytm_executor.binance_futures as binance_futures
from ytm_executor.binance_futures import BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER
from ytm_executor.preflight import preflight_command
from ytm_executor.risk import RiskPolicy, RiskState
from ytm_executor.secret_store import CredentialSummary


def test_real_binance_futures_dry_run_reaches_order_test_without_placement(monkeypatch) -> None:
    rest_api = FakeBinanceFuturesRestApi()
    monkeypatch.setattr(
        binance_futures,
        "_build_mainnet_rest_api",
        lambda *, api_key, api_secret: rest_api,
    )

    decision = preflight_command(
        _leased_real_binance_command(),
        local_credentials=(CredentialSummary(provider="binance", name="main"),),
        local_secret_resolver=lambda provider, name: {
            "apiKey": f"{provider}-{name}-public",
            "apiSecret": "binance-private-secret",
        },
        risk_policy=_risk_policy(),
        risk_state=RiskState(realized_loss_by_date={}),
        validation_summaries=(
            {
                "checkedAt": "2026-05-10T10:00:00Z",
                "name": "main",
                "permissions": {
                    "accountReadable": True,
                    "tradingAllowed": True,
                    "withdrawalsAllowed": False,
                },
                "provider": "binance",
                "status": "passed",
                "warnings": [],
            },
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert rest_api.exchange_info_calls == 1
    assert len(rest_api.test_order_calls) == 1
    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "real_execution_disabled"
    assert decision.result_payload["adapterPreflight"] == "passed"
    assert decision.result_payload["adapterResult"]["executorAction"] == "order_test_validated"
    assert decision.result_payload["adapterResult"]["normalizedOrder"]["quantity"] == 0.06
    assert "binance-private-secret" not in repr(decision.result_payload)


class FakeBinanceFuturesRestApi:
    def __init__(self) -> None:
        self.exchange_info_calls = 0
        self.test_order_calls: list[dict[str, object]] = []

    def exchange_information(self):
        self.exchange_info_calls += 1
        return {
            "symbols": [
                {
                    "filters": [
                        {
                            "filterType": "PRICE_FILTER",
                            "maxPrice": "1000000",
                            "minPrice": "0.1",
                            "tickSize": "0.1",
                        },
                        {
                            "filterType": "LOT_SIZE",
                            "maxQty": "1000",
                            "minQty": "0.001",
                            "stepSize": "0.001",
                        },
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "maxQty": "500",
                            "minQty": "0.001",
                            "stepSize": "0.001",
                        },
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                    "orderTypes": ["LIMIT", "MARKET", "STOP", "STOP_MARKET"],
                    "status": "TRADING",
                    "symbol": "BTCUSDT",
                    "timeInForce": ["GTC", "IOC"],
                }
            ]
        }

    def test_order(self, **kwargs):
        self.test_order_calls.append(kwargs)
        return {}

    def new_order(self, **kwargs):
        raise AssertionError("new_order must not be called")


def _leased_real_binance_command() -> dict[str, object]:
    return {
        "command": {
            "clientOrderId": "ytm_command_1",
            "commandPayload": {
                "adapter": BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
                "leverage": "1",
                "marginMode": "cross",
                "market": "usdm_futures",
                "orderNotional": "6",
                "orderType": "limit",
                "positionEffect": "open",
                "price": "100.06",
                "projectedPositionNotional": "6",
                "quantity": "0.0609",
            },
            "executionAccountSource": "provider",
            "executionMode": "real",
            "id": "command-1",
            "provider": "binance",
            "side": "long",
            "status": "created",
            "symbol": "BTCUSDT",
        },
        "lease": {"id": "lease-1"},
    }


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        configured=True,
        enabled=True,
        kill_switch=False,
        paper_only=False,
        allowed_markets=("usdm_futures",),
        allowed_margin_modes=("cross",),
        allowed_symbols=("BTCUSDT",),
        allowed_order_types=("limit",),
        max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("5000"),
        max_symbol_notional={"BTCUSDT": Decimal("5000")},
        max_daily_loss=Decimal("250"),
        max_leverage=Decimal("1"),
        position_mode="one_way",
    )
