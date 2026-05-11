from __future__ import annotations

from decimal import Decimal

import pytest

from ytm_executor.adapters import BrokerOrderRequest
from ytm_executor.okx_swap import (
    OKX_ORDER_PRECHECK_PATH,
    OKX_SWAP_MAINNET_ORDER_ADAPTER,
    OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
    OkxSwapMainnetOrderPlacementAdapter,
    OkxSwapMainnetOrderPrecheckAdapter,
    okx_swap_instrument_rules,
    okx_swap_order_precheck_params,
)


def test_okx_swap_order_precheck_params_for_limit_long_open() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_order_precheck_params(_request(), rules=rules)

    assert params == {
        "clOrdId": params["clOrdId"],
        "instId": "BTC-USDT-SWAP",
        "ordType": "limit",
        "posSide": "net",
        "px": "100",
        "side": "buy",
        "sz": "1.2",
        "tdMode": "cross",
    }
    assert params["clOrdId"].startswith("ytm")
    assert len(params["clOrdId"]) == 32


def test_okx_swap_maps_short_open_and_rounds_sell_price_up() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_order_precheck_params(
        _request(side="short", quantity=Decimal("1.29"), limit_price=Decimal("100.01")),
        rules=rules,
    )

    assert params["side"] == "sell"
    assert params["px"] == "100.1"
    assert params["sz"] == "1.2"


def test_okx_swap_maps_reduce_only() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_order_precheck_params(
        _request(position_effect="reduce"),
        rules=rules,
    )

    assert params["side"] == "sell"
    assert params["reduceOnly"] == "true"


def test_okx_swap_converts_notional_to_contract_quantity() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTCUSDT")

    params = okx_swap_order_precheck_params(
        _request(
            quantity=None,
            notional=Decimal("120"),
            price_reference=Decimal("100"),
            order_type="market",
        ),
        rules=rules,
    )

    assert params["instId"] == "BTC-USDT-SWAP"
    assert params["sz"] == "12"


def test_okx_swap_rejects_stop_order_type() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    with pytest.raises(ValueError, match="unsupported"):
        okx_swap_order_precheck_params(_request(order_type="stop_market"), rules=rules)


def test_okx_swap_adapter_calls_order_precheck_not_place_order() -> None:
    api = FakeOkxSwapApi()
    adapter = OkxSwapMainnetOrderPrecheckAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert len(api.precheck_calls) == 1
    assert api.precheck_calls[0]["endpoint"] == OKX_ORDER_PRECHECK_PATH
    assert api.place_order_calls == []
    assert result.payload == {
        "adapter": OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
        "clientOrderId": api.precheck_calls[0]["params"]["clOrdId"],
        "executorAction": "order_precheck_validated",
        "mainnet": True,
        "market": "okx_swap",
        "normalizedOrder": {
            "instId": "BTC-USDT-SWAP",
            "ordType": "limit",
            "posSide": "net",
            "px": "100",
            "side": "buy",
            "sz": "1.2",
            "tdMode": "cross",
        },
        "provider": "okx",
    }
    assert "private" not in repr(result.payload)
    assert "passphrase" not in repr(result.payload)


def test_okx_swap_placement_adapter_prechecks_then_places_order() -> None:
    api = FakeOkxSwapApi()
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert len(api.precheck_calls) == 1
    assert len(api.place_order_calls) == 1
    assert api.place_order_calls[0]["params"] == api.precheck_calls[0]["params"]
    assert result.payload == {
        "adapter": OKX_SWAP_MAINNET_ORDER_ADAPTER,
        "clientOrderId": api.place_order_calls[0]["params"]["clOrdId"],
        "executorAction": "order_submitted",
        "mainnet": True,
        "market": "okx_swap",
        "normalizedOrder": {
            "instId": "BTC-USDT-SWAP",
            "ordType": "limit",
            "posSide": "net",
            "px": "100",
            "side": "buy",
            "sz": "1.2",
            "tdMode": "cross",
        },
        "precheck": "passed",
        "provider": "okx",
        "providerOrderId": "okx-order-1",
        "providerResultCode": "0",
        "providerStatus": "accepted",
    }
    assert "private" not in repr(result.payload)
    assert "passphrase" not in repr(result.payload)


def test_okx_swap_placement_rejects_broker_error_without_acknowledgement() -> None:
    api = FakeOkxSwapApi(place_order_response={"code": "0", "data": [{"sCode": "51000"}]})
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    with pytest.raises(ValueError, match="51000"):
        adapter.prepare_order(_request())


class FakeOkxSwapApi:
    def __init__(self, *, place_order_response: dict[str, object] | None = None) -> None:
        self.precheck_calls: list[dict[str, object]] = []
        self.place_order_calls: list[dict[str, object]] = []
        self.place_order_response = place_order_response or {
            "code": "0",
            "data": [{"clOrdId": "ytm-order-1", "ordId": "okx-order-1", "sCode": "0"}],
            "msg": "",
        }

    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {"code": "0", "data": _instruments(), "msg": ""}

    def order_precheck(self, params: dict[str, str]) -> dict[str, object]:
        self.precheck_calls.append({"endpoint": OKX_ORDER_PRECHECK_PATH, "params": params})
        return {"code": "0", "data": [], "msg": ""}

    def place_order(self, params: dict[str, str]) -> dict[str, object]:
        self.place_order_calls.append({"params": params})
        return self.place_order_response


def _request(
    *,
    side: str = "long",
    position_effect: str = "open",
    quantity: Decimal | None = Decimal("1.29"),
    notional: Decimal | None = Decimal("120"),
    limit_price: Decimal = Decimal("100.06"),
    price_reference: Decimal | None = None,
    order_type: str = "limit",
) -> BrokerOrderRequest:
    return BrokerOrderRequest(
        provider="okx",
        execution_mode="real",
        symbol="BTC-USDT-SWAP",
        side=side,
        position_effect=position_effect,
        order_type=order_type,
        client_order_id="ytm_order_1",
        quantity=quantity,
        notional=notional,
        limit_price=limit_price,
        stop_price=None,
        price_reference=price_reference,
        time_in_force=None,
        market="okx_swap",
        margin_mode="cross",
        leverage=Decimal("1"),
    )


def _instruments() -> list[dict[str, object]]:
    return [
        {
            "ctVal": "0.1",
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "lotSz": "0.1",
            "minSz": "0.1",
            "state": "live",
            "tickSz": "0.1",
        }
    ]
