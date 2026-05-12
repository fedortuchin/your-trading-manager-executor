from __future__ import annotations

from decimal import Decimal

import pytest
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    TestOrderPositionSideEnum as BinancePositionSide,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    TestOrderSideEnum as BinanceSide,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    TestOrderTimeInForceEnum as BinanceTimeInForce,
)

from ytm_executor.adapters import BrokerOrderRequest
from ytm_executor.binance_futures import (
    BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
    BinanceUsdmFuturesMainnetOrderTestAdapter,
    binance_usdm_futures_order_test_params,
    binance_usdm_futures_symbol_rules,
)


def test_binance_usdm_futures_order_test_params_for_limit_long_open() -> None:
    params = binance_usdm_futures_order_test_params(_request())

    assert params == {
        "new_client_order_id": "ytm_order_1",
        "position_side": BinancePositionSide.BOTH,
        "price": 100.0,
        "quantity": 0.01,
        "side": BinanceSide.BUY,
        "symbol": "BTCUSDT",
        "time_in_force": BinanceTimeInForce.GTC,
        "type": "LIMIT",
    }


def test_binance_usdm_futures_maps_short_open() -> None:
    params = binance_usdm_futures_order_test_params(_request(side="short"))

    assert params["side"] == BinanceSide.SELL
    assert params["position_side"] == BinancePositionSide.BOTH
    assert "reduce_only" not in params


def test_binance_usdm_futures_maps_long_close_reduce_only() -> None:
    params = binance_usdm_futures_order_test_params(_request(position_effect="close"))

    assert params["side"] == BinanceSide.SELL
    assert params["reduce_only"] == "true"


def test_binance_usdm_futures_rejects_notional_only() -> None:
    request = _request(quantity=None, notional=Decimal("100"))

    with pytest.raises(ValueError, match="quantity"):
        binance_usdm_futures_order_test_params(request)


def test_binance_usdm_futures_normalizes_price_and_quantity_from_exchange_info() -> None:
    rules = binance_usdm_futures_symbol_rules(_exchange_info(), "BTCUSDT")
    request = _request(quantity=Decimal("0.0609"), limit_price=Decimal("100.06"))

    params = binance_usdm_futures_order_test_params(request, rules=rules)

    assert params["price"] == 100.0
    assert params["quantity"] == 0.06


def test_binance_usdm_futures_rounds_sell_limit_price_up() -> None:
    rules = binance_usdm_futures_symbol_rules(_exchange_info(), "BTCUSDT")
    request = _request(side="short", quantity=Decimal("0.0609"), limit_price=Decimal("100.01"))

    params = binance_usdm_futures_order_test_params(request, rules=rules)

    assert params["price"] == 100.1
    assert params["quantity"] == 0.06


def test_binance_usdm_futures_rejects_below_min_notional_after_normalization() -> None:
    rules = binance_usdm_futures_symbol_rules(_exchange_info(), "BTCUSDT")
    request = _request(quantity=Decimal("0.001"), limit_price=Decimal("100"))

    with pytest.raises(ValueError, match="minNotional"):
        binance_usdm_futures_order_test_params(request, rules=rules)


def test_binance_usdm_futures_adapter_calls_test_order_not_new_order() -> None:
    class FakeRestApi:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def exchange_information(self):
            return _exchange_info()

        def test_order(self, **kwargs):
            self.calls.append(kwargs)
            return {}

        def new_order(self, **kwargs):
            raise AssertionError("new_order must not be called")

    rest_api = FakeRestApi()
    adapter = BinanceUsdmFuturesMainnetOrderTestAdapter(
        api_key="public",
        api_secret="private",
        rest_api=rest_api,
    )

    result = adapter.prepare_order(_request(quantity=Decimal("0.1")))

    assert len(rest_api.calls) == 1
    assert rest_api.calls[0]["symbol"] == "BTCUSDT"
    assert result.payload == {
        "adapter": BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
        "clientOrderId": "ytm_order_1",
        "executorAction": "order_test_validated",
        "mainnet": True,
        "market": "usdm_futures",
        "normalizedOrder": {
            "positionSide": "BOTH",
            "price": 100.0,
            "quantity": 0.1,
            "side": "BUY",
            "symbol": "BTCUSDT",
            "timeInForce": "GTC",
            "type": "LIMIT",
        },
        "provider": "binance",
    }
    assert "private" not in repr(result.payload)


def _request(
    *,
    side: str = "long",
    position_effect: str = "open",
    quantity: Decimal | None = Decimal("0.01"),
    notional: Decimal | None = None,
    limit_price: Decimal = Decimal("100"),
) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        provider="binance",
        execution_mode="real",
        symbol="BTCUSDT",
        side=side,
        position_effect=position_effect,
        order_type="limit",
        client_order_id="ytm_order_1",
        quantity=quantity,
        notional=notional,
        limit_price=limit_price,
        stop_price=None,
        stop_loss=None,
        price_reference=None,
        time_in_force=None,
    )


def _exchange_info() -> dict[str, object]:
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
