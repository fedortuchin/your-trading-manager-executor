from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from ytm_executor.adapters import BrokerOrderRequest
from ytm_executor.binance_spot import (
    BINANCE_SPOT_TESTNET_ORDER_TEST_ADAPTER,
    BinanceSpotTestnetOrderTestAdapter,
    binance_order_test_params,
)


@dataclass(slots=True)
class FakeBinanceRestApi:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def order_test(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return object()


def test_binance_order_test_params_for_limit_buy() -> None:
    params = binance_order_test_params(_request())

    assert params == {
        "new_client_order_id": "ytm_order_1",
        "price": 100.0,
        "quantity": 0.01,
        "side": "BUY",
        "symbol": "BTCUSDT",
        "time_in_force": "GTC",
        "type": "LIMIT",
    }


def test_binance_testnet_adapter_calls_order_test_not_new_order() -> None:
    api = FakeBinanceRestApi()
    adapter = BinanceSpotTestnetOrderTestAdapter(
        api_key="public",
        api_secret="private",
        rest_api=api,
    )

    result = adapter.prepare_order(_request())

    assert len(api.calls) == 1
    assert api.calls[0]["new_client_order_id"] == "ytm_order_1"
    assert result.status == "acknowledged"
    assert result.payload == {
        "adapter": BINANCE_SPOT_TESTNET_ORDER_TEST_ADAPTER,
        "clientOrderId": "ytm_order_1",
        "executorAction": "order_test_validated",
        "provider": "binance",
        "testnet": True,
    }
    assert "private" not in repr(result.payload)


def test_binance_spot_rejects_short_open() -> None:
    request = _request(side="short")

    with pytest.raises(ValueError, match="long spot"):
        binance_order_test_params(request)


def _request(*, side: str = "long") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id="ytm_order_1",
        execution_mode="external_paper",
        limit_price=Decimal("100"),
        notional=None,
        order_type="limit",
        position_effect="open",
        provider="binance",
        quantity=Decimal("0.01"),
        side=side,
        stop_price=None,
        symbol="BTCUSDT",
        time_in_force=None,
    )
