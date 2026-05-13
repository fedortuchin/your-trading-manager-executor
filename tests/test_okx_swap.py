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
    okx_swap_set_leverage_params,
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
                "tpOrdPx": "-1",
                "tpTriggerPx": "110",
                "tpTriggerPxType": "last",
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
    assert params["attachAlgoOrds"][0]["attachAlgoClOrdId"].startswith("ytmps")
    assert len(params["attachAlgoOrds"][0]["attachAlgoClOrdId"]) == 32


def test_okx_swap_maps_short_open_and_rounds_sell_price_up() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_order_precheck_params(
        _request(
            side="short",
            quantity=Decimal("1.29"),
            limit_price=Decimal("100.01"),
            stop_loss=Decimal("105"),
            take_profit=Decimal("95"),
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
    assert params["ordType"] == "ioc"
    assert params["px"] == "100.1"


def test_okx_swap_rejects_real_market_order_without_slippage_cap() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTCUSDT")

    with pytest.raises(ValueError, match="maxSlippageBps"):
        okx_swap_order_precheck_params(
            _request(
                quantity=None,
                notional=Decimal("120"),
                price_reference=Decimal("100"),
                order_type="market",
                max_slippage_bps=None,
            ),
            rules=rules,
        )


def test_okx_swap_set_leverage_params_rounds_up_to_integer() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    params = okx_swap_set_leverage_params(
        _request(leverage=Decimal("4.2")),
        rules=rules,
    )

    assert params == {
        "instId": "BTC-USDT-SWAP",
        "lever": "5",
        "mgnMode": "cross",
    }


def test_okx_swap_real_open_requires_stop_loss() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    with pytest.raises(ValueError, match="stopLoss"):
        okx_swap_order_precheck_params(_request(stop_loss=None), rules=rules)


def test_okx_swap_real_open_requires_take_profit() -> None:
    rules = okx_swap_instrument_rules(_instruments(), "BTC-USDT-SWAP")

    with pytest.raises(ValueError, match="takeProfit"):
        okx_swap_order_precheck_params(_request(take_profit=None), rules=rules)


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

    assert api.set_leverage_calls == []
    assert len(api.precheck_calls) == 1
    assert api.precheck_calls[0]["endpoint"] == OKX_ORDER_PRECHECK_PATH
    assert api.place_order_calls == []
    normalized = result.payload["normalizedOrder"]
    assert result.payload["adapter"] == OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER
    assert result.payload["accountConfig"] == {"acctLv": "3", "posMode": "net_mode"}
    assert result.payload["clientOrderId"] == api.precheck_calls[0]["params"]["clOrdId"]
    assert result.payload["executorAction"] == "order_precheck_validated"
    assert result.payload["precheck"] == {"status": "passed"}
    assert normalized["attachedStopLoss"]["slTriggerPx"] == "95"
    assert normalized["attachedTakeProfit"]["tpTriggerPx"] == "110"
    assert normalized["instId"] == "BTC-USDT-SWAP"
    assert normalized["ordType"] == "limit"
    assert normalized["px"] == "100"
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

    assert api.set_leverage_calls == [
        {"params": {"instId": "BTC-USDT-SWAP", "lever": "1", "mgnMode": "cross"}}
    ]
    assert len(api.precheck_calls) == 1
    assert len(api.place_order_calls) == 1
    assert api.place_order_calls[0]["params"] == api.precheck_calls[0]["params"]
    normalized = result.payload["normalizedOrder"]
    protection = result.payload["protection"]
    assert result.payload["adapter"] == OKX_SWAP_MAINNET_ORDER_ADAPTER
    assert result.payload["accountConfig"] == {"acctLv": "3", "posMode": "net_mode"}
    assert result.payload["clientOrderId"] == api.place_order_calls[0]["params"]["clOrdId"]
    assert result.payload["executorAction"] == "order_submitted"
    assert result.payload["leverage"] == {
        "instId": "BTC-USDT-SWAP",
        "lever": "1",
        "mgnMode": "cross",
        "verified": "true",
    }
    assert result.payload["protectionStatus"] == "protected"
    assert result.payload["providerOrderId"] == "okx-order-1"
    assert result.payload["providerStatus"] == "accepted"
    assert result.payload["precheck"] == {"status": "passed"}
    assert normalized["attachedStopLoss"]["slTriggerPx"] == "95"
    assert normalized["attachedTakeProfit"]["tpTriggerPx"] == "110"
    assert normalized["ordType"] == "limit"
    assert protection["slTriggerPx"] == "95"
    assert protection["tpTriggerPx"] == "110"
    assert protection["status"] == "protected"
    assert "private" not in repr(result.payload)
    assert "passphrase" not in repr(result.payload)


def test_okx_swap_precheck_skips_order_precheck_in_futures_mode() -> None:
    api = FakeOkxSwapApi(account_config={"acctLv": "2", "posMode": "net_mode"})
    adapter = OkxSwapMainnetOrderPrecheckAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert api.precheck_calls == []
    assert api.place_order_calls == []
    assert result.payload["executorAction"] == "order_precheck_skipped"
    assert result.payload["precheck"]["reasonCode"] == "unsupported_for_futures_mode"
    assert result.payload["precheck"]["status"] == "skipped"


def test_okx_swap_placement_skips_order_precheck_in_futures_mode() -> None:
    api = FakeOkxSwapApi(account_config={"acctLv": "2", "posMode": "net_mode"})
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert api.precheck_calls == []
    assert len(api.place_order_calls) == 1
    assert result.payload["executorAction"] == "order_submitted"
    assert result.payload["precheck"]["reasonCode"] == "unsupported_for_futures_mode"
    assert result.payload["precheck"]["status"] == "skipped"


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


def test_okx_swap_placement_rejects_unsupported_account_mode() -> None:
    api = FakeOkxSwapApi(account_config={"acctLv": "1", "posMode": "net_mode"})
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    with pytest.raises(ValueError, match="account mode"):
        adapter.prepare_order(_request())


def test_okx_swap_placement_rejects_leverage_response_mismatch() -> None:
    api = FakeOkxSwapApi(
        set_leverage_response={"code": "0", "data": [{"lever": "2", "mgnMode": "cross"}]}
    )
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    with pytest.raises(ValueError, match="set_leverage"):
        adapter.prepare_order(_request())


def test_okx_swap_placement_recovers_existing_order_by_client_id() -> None:
    api = FakeOkxSwapApi(existing_order={"ordId": "okx-existing-1", "sCode": "0"})
    adapter = OkxSwapMainnetOrderPlacementAdapter(
        api_key="public",
        api_secret="private",
        passphrase="passphrase",
        api=api,
    )

    result = adapter.prepare_order(_request())

    assert api.get_order_calls == [
        {
            "clOrdId": api.precheck_calls[0]["params"]["clOrdId"],
            "instId": "BTC-USDT-SWAP",
        }
    ]
    assert api.place_order_calls == []
    assert result.payload["idempotencyRecovery"] is True
    assert result.payload["providerOrderId"] == "okx-existing-1"


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
        set_leverage_response: dict[str, object] | None = None,
        account_config: dict[str, object] | None = None,
        existing_order: dict[str, object] | None = None,
        positions: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.precheck_calls: list[dict[str, object]] = []
        self.set_leverage_calls: list[dict[str, object]] = []
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
        self.set_leverage_response = set_leverage_response
        self.account_config = account_config or {"acctLv": "3", "posMode": "net_mode"}
        self.existing_order = existing_order
        self.positions = positions
        self.get_order_calls: list[dict[str, object]] = []

    def get_account_config(self) -> dict[str, object]:
        return {"code": "0", "data": [self.account_config], "msg": ""}

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

    def get_order(self, *, inst_id: str, cl_ord_id: str) -> dict[str, object]:
        self.get_order_calls.append({"instId": inst_id, "clOrdId": cl_ord_id})
        if self.existing_order is not None:
            return {"code": "0", "data": [self.existing_order], "msg": ""}
        return {"code": "51603", "data": [], "msg": "order does not exist"}

    def order_algos_pending(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        if self.pending_algos is not None:
            return {"code": "0", "data": list(self.pending_algos), "msg": ""}
        params = (
            self.place_order_calls[0]["params"]
            if self.place_order_calls
            else self.precheck_calls[0]["params"]
        )
        attach = params["attachAlgoOrds"][0]
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
                    "tpOrdPx": attach.get("tpOrdPx"),
                    "tpTriggerPx": attach.get("tpTriggerPx"),
                    "tpTriggerPxType": attach.get("tpTriggerPxType"),
                    "state": "live",
                }
            ],
            "msg": "",
        }

    def get_positions(self, *, inst_type: str, inst_id: str) -> dict[str, object]:
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {"code": "0", "data": list(self.positions), "msg": ""}

    def set_leverage(self, params: dict[str, object]) -> dict[str, object]:
        self.set_leverage_calls.append({"params": params})
        if self.set_leverage_response is not None:
            return self.set_leverage_response
        return {
            "code": "0",
            "data": [
                {
                    "instId": params["instId"],
                    "lever": params["lever"],
                    "mgnMode": params["mgnMode"],
                }
            ],
        }

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
    take_profit: Decimal | None = Decimal("110"),
    leverage: Decimal | None = Decimal("1"),
    max_slippage_bps: Decimal | None = Decimal("10"),
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
        take_profit_targets=() if take_profit is None else (take_profit,),
        price_reference=price_reference,
        time_in_force=None,
        max_slippage_bps=max_slippage_bps,
        market="okx_swap",
        margin_mode="cross",
        leverage=leverage,
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
