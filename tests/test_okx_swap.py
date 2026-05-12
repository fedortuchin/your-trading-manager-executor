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
        "attachAlgoOrds": [
            {
                "attachAlgoClOrdId": params["attachAlgoOrds"][0]["attachAlgoClOrdId"],
                "slOrdPx": "-1",
                "slTriggerPx": "95",
                "slTriggerPxType": "last",
            }
        ],
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
    assert params["attachAlgoOrds"][0]["attachAlgoClOrdId"].startswith("ytmsl")
    assert len(params["attachAlgoOrds"][0]["attachAlgoClOrdId"]) == 32


def test_okx_swap_maps_short_open_and_rounds_sell_price_up() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_order_precheck_params(
        _request(
            side="short",
            quantity=Decimal("1.29"),
            limit_price=Decimal("100.01"),
            stop_loss=Decimal("105"),
        ),
        rules=rules,
    )

    assert params["side"] == "sell"
    assert params["px"] == "100.1"
    assert params["sz"] == "1.2"
    assert params["attachAlgoOrds"][0]["slTriggerPx"] == "105"


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


def test_okx_swap_real_open_requires_stop_loss() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    with pytest.raises(ValueError, match="stopLoss"):
        okx_swap_order_precheck_params(_request(stop_loss=None), rules=rules)


def test_okx_swap_rejects_stop_loss_on_wrong_side() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    with pytest.raises(ValueError, match="long stopLoss"):
        okx_swap_order_precheck_params(_request(stop_loss=Decimal("101")), rules=rules)


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
                "attachedStopLoss": {
                    "attachAlgoClOrdId": api.precheck_calls[0]["params"]["attachAlgoOrds"][0][
                        "attachAlgoClOrdId"
                    ],
                    "orderPrice": "market",
                    "slOrdPx": "-1",
                    "slTriggerPx": "95",
                    "slTriggerPxType": "last",
                },
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
            "attachedStopLoss": {
                "attachAlgoClOrdId": api.place_order_calls[0]["params"]["attachAlgoOrds"][0][
                    "attachAlgoClOrdId"
                ],
                "orderPrice": "market",
                "slOrdPx": "-1",
                "slTriggerPx": "95",
                "slTriggerPxType": "last",
            },
            "instId": "BTC-USDT-SWAP",
            "ordType": "limit",
            "posSide": "net",
            "px": "100",
            "side": "buy",
            "sz": "1.2",
            "tdMode": "cross",
        },
        "precheck": "passed",
        "protection": {
            "algoClientOrderId": api.place_order_calls[0]["params"]["attachAlgoOrds"][0][
                "attachAlgoClOrdId"
            ],
            "algoOrderId": "algo-sl-1",
            "slOrdPx": "-1",
            "slTriggerPx": "95",
            "slTriggerPxType": "last",
            "status": "protected",
            "verification": "pending_algo_order",
        },
        "protectionStatus": "protected",
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


def test_okx_swap_placement_remediates_missing_attached_stop_loss() -> None:
    api = FakeOkxSwapApi(pending_algos=(), positions=({"instId": "BTC-USDT-SWAP", "pos": "1.2"},))
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert len(api.place_algo_order_calls) == 1
    assert api.place_algo_order_calls[0]["params"]["ordType"] == "conditional"
    assert api.place_algo_order_calls[0]["params"]["reduceOnly"] == "true"
    assert result.payload["protectionStatus"] == "protected_remediated"
    assert result.payload["protection"]["status"] == "protected_remediated"


def test_okx_swap_placement_marks_unprotected_when_remediation_fails() -> None:
    api = FakeOkxSwapApi(
        pending_algos=(),
        positions=({"instId": "BTC-USDT-SWAP", "pos": "1.2"},),
        place_algo_order_response={"code": "0", "data": [{"sCode": "51000"}]},
    )
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert result.payload["protectionStatus"] == "unprotected"
    assert result.payload["protection"]["actionRequired"] == "manual_intervention"


def test_okx_swap_placement_marks_pending_activation_without_open_position() -> None:
    api = FakeOkxSwapApi(pending_algos=(), positions=())
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert result.payload["protectionStatus"] == "pending_activation"
    assert result.payload["protection"]["reasonCode"] == "parent_order_not_filled"


class FakeOkxSwapApi:
    def __init__(
        self,
        *,
        pending_algos: tuple[dict[str, object], ...] | None = None,
        place_algo_order_response: dict[str, object] | None = None,
        place_order_response: dict[str, object] | None = None,
        positions: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.precheck_calls: list[dict[str, object]] = []
        self.place_algo_order_calls: list[dict[str, object]] = []
        self.place_order_calls: list[dict[str, object]] = []
        self.pending_algos = pending_algos
        self.place_algo_order_response = place_algo_order_response or {
            "code": "0",
            "data": [{"algoId": "algo-remediated-1", "sCode": "0"}],
            "msg": "",
        }
        self.place_order_response = place_order_response or {
            "code": "0",
            "data": [{"clOrdId": "ytm-order-1", "ordId": "okx-order-1", "sCode": "0"}],
            "msg": "",
        }
        self.positions = positions

    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {"code": "0", "data": _instruments(), "msg": ""}

    def order_precheck(self, params: dict[str, object]) -> dict[str, object]:
        self.precheck_calls.append({"endpoint": OKX_ORDER_PRECHECK_PATH, "params": params})
        return {"code": "0", "data": [], "msg": ""}

    def place_order(self, params: dict[str, object]) -> dict[str, object]:
        self.place_order_calls.append({"params": params})
        return self.place_order_response

    def order_algos_pending(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        if self.pending_algos is not None:
            return {"code": "0", "data": list(self.pending_algos), "msg": ""}
        attach = self.place_order_calls[0]["params"]["attachAlgoOrds"][0]
        return {
            "code": "0",
            "data": [
                {
                    "algoClOrdId": attach["attachAlgoClOrdId"],
                    "algoId": "algo-sl-1",
                    "ordId": "okx-order-1",
                    "slOrdPx": attach["slOrdPx"],
                    "slTriggerPx": attach["slTriggerPx"],
                    "slTriggerPxType": attach["slTriggerPxType"],
                    "state": "live",
                }
            ],
            "msg": "",
        }

    def get_positions(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {"code": "0", "data": list(self.positions), "msg": ""}

    def place_algo_order(self, params: dict[str, object]) -> dict[str, object]:
        self.place_algo_order_calls.append({"params": params})
        return self.place_algo_order_response


def _request(
    *,
    side: str = "long",
    position_effect: str = "open",
    quantity: Decimal | None = Decimal("1.29"),
    notional: Decimal | None = Decimal("120"),
    limit_price: Decimal = Decimal("100.06"),
    price_reference: Decimal | None = None,
    order_type: str = "limit",
    stop_loss: Decimal | None = Decimal("95"),
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
        stop_loss=stop_loss,
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
