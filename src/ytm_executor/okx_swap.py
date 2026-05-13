"""OKX SWAP mainnet validate-only adapter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Protocol

from ytm_executor.adapters import BrokerAdapterResult, BrokerOrderRequest
from ytm_executor.guards import reject_secret_fields

OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER = "okx_swap_mainnet_order_precheck"
OKX_SWAP_MAINNET_ORDER_ADAPTER = "okx_swap_mainnet_order"
OKX_ORDER_PRECHECK_PATH = "/api/v5/trade/order-precheck"


class OkxSwapApi(Protocol):
    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, Any]: ...
    def get_positions(self, *, inst_type: str, inst_id: str) -> dict[str, Any]: ...
    def set_leverage(self, params: dict[str, Any]) -> dict[str, Any]: ...
    def order_precheck(self, params: dict[str, Any]) -> dict[str, Any]: ...
    def place_order(self, params: dict[str, Any]) -> dict[str, Any]: ...
    def order_algos_pending(self, *, inst_type: str, inst_id: str) -> dict[str, Any]: ...
    def place_algo_order(self, params: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class OkxSwapInstrumentRules:
    inst_id: str
    inst_type: str
    state: str
    contract_value: Decimal
    lot_size: Decimal
    tick_size: Decimal
    min_size: Decimal


@dataclass(frozen=True, slots=True)
class OkxSwapMainnetOrderPrecheckAdapter:
    """Validate OKX SWAP mainnet orders without placing them."""

    api_key: str
    api_secret: str
    passphrase: str
    api: OkxSwapApi | None = None

    provider = "okx"

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult:
        if request.provider != "okx":
            raise ValueError("OKX adapter received a non-OKX request")
        if request.execution_mode not in {"external_paper", "real"}:
            raise ValueError("OKX SWAP order precheck requires provider-backed execution")
        api = self.api or _build_mainnet_api(
            api_key=self.api_key,
            api_secret=self.api_secret,
            passphrase=self.passphrase,
        )
        inst_id = okx_swap_instrument_id(request.symbol)
        rules = okx_swap_instrument_rules(
            _okx_response_data(
                api.get_instruments(inst_type="SWAP", inst_id=inst_id),
                "get_instruments",
            ),
            inst_id,
        )
        params = okx_swap_order_precheck_params(request, rules=rules)
        _okx_response_data(api.order_precheck(params), "order_precheck")
        payload = {
            "adapter": OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
            "clientOrderId": params["clOrdId"],
            "executorAction": "order_precheck_validated",
            "mainnet": True,
            "market": "okx_swap",
            "normalizedOrder": _public_normalized_order(params),
            "provider": "okx",
        }
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


@dataclass(frozen=True, slots=True)
class OkxSwapMainnetOrderPlacementAdapter:
    """Place OKX SWAP mainnet orders after local gates and OKX precheck pass."""

    api_key: str
    api_secret: str
    passphrase: str
    api: OkxSwapApi | None = None

    provider = "okx"

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult:
        if request.provider != "okx":
            raise ValueError("OKX adapter received a non-OKX request")
        if request.execution_mode != "real":
            raise ValueError("OKX SWAP order placement requires executionMode=real")
        api = self.api or _build_mainnet_api(
            api_key=self.api_key,
            api_secret=self.api_secret,
            passphrase=self.passphrase,
        )
        inst_id = okx_swap_instrument_id(request.symbol)
        rules = okx_swap_instrument_rules(
            _okx_response_data(
                api.get_instruments(inst_type="SWAP", inst_id=inst_id),
                "get_instruments",
            ),
            inst_id,
        )
        leverage_params = okx_swap_set_leverage_params(request, rules=rules)
        if leverage_params is not None:
            _okx_response_data(api.set_leverage(leverage_params), "set_leverage")
        params = okx_swap_order_params(request, rules=rules)
        _okx_response_data(api.order_precheck(params), "order_precheck")
        response = _single_order_response(
            _okx_response_data(api.place_order(params), "place_order"),
            "place_order",
        )
        provider_order_id = _optional_text(response.get("ordId"))
        if provider_order_id is None:
            raise ValueError("OKX place_order response missing ordId")
        protection = _verify_or_remediate_stop_loss(
            api=api,
            request=request,
            order_params=params,
            order_response=response,
            provider_order_id=provider_order_id,
            rules=rules,
        )
        payload = {
            "adapter": OKX_SWAP_MAINNET_ORDER_ADAPTER,
            "clientOrderId": params["clOrdId"],
            "executorAction": "order_submitted",
            "mainnet": True,
            "market": "okx_swap",
            "leverage": leverage_params,
            "normalizedOrder": _public_normalized_order(params),
            "precheck": "passed",
            "protection": protection,
            "protectionStatus": protection["status"],
            "provider": "okx",
            "providerOrderId": provider_order_id,
            "providerStatus": "accepted",
        }
        result_code = _optional_text(response.get("sCode"))
        result_message = _optional_text(response.get("sMsg"))
        if result_code is not None:
            payload["providerResultCode"] = result_code
        if result_message is not None:
            payload["providerMessage"] = result_message
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


@dataclass(frozen=True, slots=True)
class OkxSdkSwapApi:
    account_api: Any
    trade_api: Any

    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, Any]:
        return self.account_api.get_instruments(instType=inst_type, instId=inst_id)

    def get_positions(self, *, inst_type: str, inst_id: str) -> dict[str, Any]:
        return self.account_api.get_positions(instType=inst_type, instId=inst_id)

    def set_leverage(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.account_api.set_leverage(**params)

    def order_precheck(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.trade_api._request_with_params("POST", OKX_ORDER_PRECHECK_PATH, params)

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.trade_api.place_order(**params)

    def order_algos_pending(self, *, inst_type: str, inst_id: str) -> dict[str, Any]:
        return self.trade_api.order_algos_list(instType=inst_type, instId=inst_id)

    def place_algo_order(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.trade_api.place_algo_order(**params)


def okx_swap_order_precheck_params(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, Any]:
    return okx_swap_order_params(request, rules=rules)


def okx_swap_order_params(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, Any]:
    order_type = _okx_order_type(request.order_type)
    side = _okx_side(request)
    size = _normalized_size(request, rules=rules)
    params: dict[str, Any] = {
        "clOrdId": _okx_client_order_id(request.client_order_id),
        "instId": rules.inst_id,
        "ordType": order_type,
        "posSide": "net",
        "side": side,
        "sz": _decimal_text(size),
        "tdMode": _okx_td_mode(request.margin_mode),
    }
    if request.position_effect in {"reduce", "close"}:
        params["reduceOnly"] = "true"
    if order_type == "limit":
        if request.limit_price is None:
            raise ValueError("OKX SWAP limit orders require price")
        params["px"] = _decimal_text(
            _normalized_price(request.limit_price, side=side, rules=rules)
        )
    if request.position_effect == "open":
        if request.execution_mode == "real" and request.stop_loss is None:
            raise ValueError("OKX real open orders require exchange-side stopLoss")
        if request.stop_loss is not None:
            params["attachAlgoOrds"] = [_okx_attached_stop_loss(request, rules=rules)]
    reject_secret_fields(params)
    return params


def okx_swap_set_leverage_params(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, str] | None:
    if request.leverage is None:
        return None
    return {
        "instId": rules.inst_id,
        "lever": _integer_leverage_text(request.leverage),
        "mgnMode": _okx_td_mode(request.margin_mode),
    }


def okx_swap_instrument_rules(
    items: list[dict[str, Any]],
    inst_id: str,
) -> OkxSwapInstrumentRules:
    normalized = okx_swap_instrument_id(inst_id)
    for item in items:
        if str(item.get("instId") or "").upper() == normalized:
            rules = OkxSwapInstrumentRules(
                inst_id=_required_text(item.get("instId"), "instId").upper(),
                inst_type=_required_text(item.get("instType"), "instType").upper(),
                state=_required_text(item.get("state"), "state").lower(),
                contract_value=_required_positive_decimal(item.get("ctVal"), "ctVal"),
                lot_size=_required_positive_decimal(item.get("lotSz"), "lotSz"),
                tick_size=_required_positive_decimal(item.get("tickSz"), "tickSz"),
                min_size=_required_positive_decimal(item.get("minSz"), "minSz"),
            )
            if rules.inst_type != "SWAP":
                raise ValueError("OKX instrument is not SWAP")
            if rules.state != "live":
                raise ValueError("OKX SWAP instrument is not live")
            return rules
    raise ValueError(f"OKX SWAP instrument is not found: {normalized}")


def _build_mainnet_api(*, api_key: str, api_secret: str, passphrase: str) -> OkxSwapApi:
    import okx.Account as Account
    import okx.Trade as Trade

    return OkxSdkSwapApi(
        account_api=Account.AccountAPI(api_key, api_secret, passphrase, False, "0"),
        trade_api=Trade.TradeAPI(api_key, api_secret, passphrase, False, "0"),
    )


def okx_swap_instrument_id(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if "-" in normalized:
        return normalized
    if normalized.endswith("USDT") and len(normalized) > 4:
        return f"{normalized[:-4]}-USDT-SWAP"
    return normalized


def _okx_response_data(response: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        raise ValueError(f"OKX {endpoint} response is invalid")
    code = str(response.get("code") or "")
    if code != "0":
        raise ValueError(f"OKX {endpoint} rejected request with code {code or 'unknown'}")
    data = response.get("data", [])
    if data in ("", None):
        data = []
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"OKX {endpoint} response data is invalid")
    reject_secret_fields(data)
    return [dict(item) for item in data]


def _single_order_response(items: list[dict[str, Any]], endpoint: str) -> dict[str, Any]:
    if not items:
        raise ValueError(f"OKX {endpoint} response data is empty")
    response = dict(items[0])
    status_code = _optional_text(response.get("sCode"))
    if status_code not in {None, "0"}:
        raise ValueError(f"OKX {endpoint} rejected request with code {status_code}")
    reject_secret_fields(response)
    return response


def _verify_or_remediate_stop_loss(
    *,
    api: OkxSwapApi,
    request: BrokerOrderRequest,
    order_params: dict[str, Any],
    order_response: dict[str, Any],
    provider_order_id: str,
    rules: OkxSwapInstrumentRules,
) -> dict[str, Any]:
    expected = _expected_stop_loss(order_params)
    if expected is None:
        return {
            "reasonCode": "stop_loss_not_required",
            "status": "not_required",
        }

    attach_response = _attached_stop_loss_response(
        order_response.get("attachAlgoOrds"),
        expected=expected,
    )
    if attach_response is not None:
        fail_code = _optional_text(attach_response.get("failCode"))
        if fail_code is not None:
            return _remediate_stop_loss(
                api=api,
                request=request,
                provider_order_id=provider_order_id,
                rules=rules,
                expected=expected,
                reason_code="attached_stop_loss_rejected",
                reason=(
                    _optional_text(attach_response.get("failReason"))
                    or f"OKX attached stop-loss failed with code {fail_code}"
                ),
            )

    pending_match = _matching_pending_stop_loss(
        _okx_response_data(
            api.order_algos_pending(inst_type="SWAP", inst_id=rules.inst_id),
            "order_algos_pending",
        ),
        expected=expected,
        provider_order_id=provider_order_id,
    )
    if pending_match is not None:
        return {
            "algoClientOrderId": _optional_text(pending_match.get("algoClOrdId")),
            "algoOrderId": _optional_text(pending_match.get("algoId")),
            "slOrdPx": expected["slOrdPx"],
            "slTriggerPx": expected["slTriggerPx"],
            "slTriggerPxType": expected["slTriggerPxType"],
            "status": "protected",
            "verification": "pending_algo_order",
        }

    position = _matching_open_position(
        _okx_response_data(
            api.get_positions(inst_type="SWAP", inst_id=rules.inst_id),
            "get_positions",
        ),
        rules=rules,
    )
    if position is None:
        return {
            "algoClientOrderId": expected["attachAlgoClOrdId"],
            "reasonCode": "parent_order_not_filled",
            "slOrdPx": expected["slOrdPx"],
            "slTriggerPx": expected["slTriggerPx"],
            "slTriggerPxType": expected["slTriggerPxType"],
            "status": "pending_activation",
            "verification": "no_open_position",
        }
    return _remediate_stop_loss(
        api=api,
        request=request,
        provider_order_id=provider_order_id,
        rules=rules,
        expected=expected,
        open_position=position,
        reason_code="active_stop_loss_not_found",
        reason="Open OKX position has no matching active stop-loss algo order.",
    )


def _expected_stop_loss(order_params: dict[str, Any]) -> dict[str, str] | None:
    attach_algo_orders = order_params.get("attachAlgoOrds")
    if not isinstance(attach_algo_orders, list):
        return None
    for item in attach_algo_orders:
        if not isinstance(item, dict):
            continue
        trigger_price = _optional_text(item.get("slTriggerPx"))
        order_price = _optional_text(item.get("slOrdPx"))
        client_order_id = _optional_text(item.get("attachAlgoClOrdId"))
        if trigger_price is None or order_price is None or client_order_id is None:
            continue
        return {
            "attachAlgoClOrdId": client_order_id,
            "slOrdPx": order_price,
            "slTriggerPx": trigger_price,
            "slTriggerPxType": _optional_text(item.get("slTriggerPxType")) or "last",
        }
    return None


def _attached_stop_loss_response(
    value: object,
    *,
    expected: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        algo_client_order_id = _optional_text(item.get("attachAlgoClOrdId"))
        if algo_client_order_id == expected["attachAlgoClOrdId"]:
            return dict(item)
    return None


def _matching_pending_stop_loss(
    items: list[dict[str, Any]],
    *,
    expected: dict[str, str],
    provider_order_id: str,
) -> dict[str, Any] | None:
    for item in items:
        algo_client_order_id = _optional_text(item.get("algoClOrdId"))
        linked_order_id = _optional_text(item.get("ordId"))
        trigger_price = _optional_text(item.get("slTriggerPx"))
        order_price = _optional_text(item.get("slOrdPx"))
        state = (_optional_text(item.get("state")) or "").lower()
        if state and state not in {"live", "effective", "partially_effective"}:
            continue
        if trigger_price != expected["slTriggerPx"] or order_price != expected["slOrdPx"]:
            continue
        if algo_client_order_id == expected["attachAlgoClOrdId"]:
            return dict(item)
        if linked_order_id and linked_order_id == provider_order_id:
            return dict(item)
    return None


def _matching_open_position(
    items: list[dict[str, Any]],
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("instId") or "").upper() != rules.inst_id:
            continue
        size = _optional_abs_decimal(item.get("pos"), "pos")
        if size is not None and size > 0:
            result = dict(item)
            result["normalizedSize"] = _decimal_text(size)
            return result
    return None


def _remediate_stop_loss(
    *,
    api: OkxSwapApi,
    request: BrokerOrderRequest,
    provider_order_id: str,
    rules: OkxSwapInstrumentRules,
    expected: dict[str, str],
    reason_code: str,
    reason: str,
    open_position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    position = open_position
    if position is None:
        position = _matching_open_position(
            _okx_response_data(
                api.get_positions(inst_type="SWAP", inst_id=rules.inst_id),
                "get_positions",
            ),
            rules=rules,
        )
    if position is None:
        return {
            "algoClientOrderId": expected["attachAlgoClOrdId"],
            "reason": reason,
            "reasonCode": reason_code,
            "slOrdPx": expected["slOrdPx"],
            "slTriggerPx": expected["slTriggerPx"],
            "slTriggerPxType": expected["slTriggerPxType"],
            "status": "pending_activation",
            "verification": "no_open_position_after_attach_failure",
        }
    try:
        remediation_params = _remediation_stop_loss_params(
            request=request,
            provider_order_id=provider_order_id,
            position=position,
            rules=rules,
            expected=expected,
        )
        response = _single_order_response(
            _okx_response_data(api.place_algo_order(remediation_params), "place_algo_order"),
            "place_algo_order",
        )
    except ValueError as exc:
        return {
            "actionRequired": "manual_intervention",
            "reason": str(exc),
            "reasonCode": "stop_loss_remediation_failed",
            "slOrdPx": expected["slOrdPx"],
            "slTriggerPx": expected["slTriggerPx"],
            "slTriggerPxType": expected["slTriggerPxType"],
            "status": "unprotected",
            "verification": reason_code,
        }
    return {
        "algoClientOrderId": remediation_params["algoClOrdId"],
        "algoOrderId": _optional_text(response.get("algoId")),
        "remediated": True,
        "remediationReasonCode": reason_code,
        "slOrdPx": expected["slOrdPx"],
        "slTriggerPx": expected["slTriggerPx"],
        "slTriggerPxType": expected["slTriggerPxType"],
        "status": "protected_remediated",
        "verification": "standalone_stop_loss_algo_order",
    }


def _remediation_stop_loss_params(
    *,
    request: BrokerOrderRequest,
    provider_order_id: str,
    position: dict[str, Any],
    rules: OkxSwapInstrumentRules,
    expected: dict[str, str],
) -> dict[str, Any]:
    size = _optional_abs_decimal(position.get("pos"), "pos")
    if size is None or size <= 0:
        raise ValueError("OKX open position size is missing for stop-loss remediation")
    position_side = _okx_position_close_side(position=position, request=request)
    return {
        "algoClOrdId": _okx_attached_algo_client_order_id(
            f"{request.client_order_id}:{provider_order_id}",
            "sr",
        ),
        "instId": rules.inst_id,
        "ordType": "conditional",
        "posSide": "net",
        "reduceOnly": "true",
        "side": position_side,
        "slOrdPx": expected["slOrdPx"],
        "slTriggerPx": expected["slTriggerPx"],
        "slTriggerPxType": expected["slTriggerPxType"],
        "sz": _decimal_text(_round_down_to_step(size, rules.lot_size)),
        "tdMode": _okx_td_mode(request.margin_mode),
    }


def _okx_position_close_side(*, position: dict[str, Any], request: BrokerOrderRequest) -> str:
    raw_pos = _optional_decimal(position.get("pos"), "pos")
    if raw_pos is not None and raw_pos < 0:
        return "buy"
    if raw_pos is not None and raw_pos > 0:
        return "sell"
    return "sell" if request.side == "long" else "buy"


def _normalized_size(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> Decimal:
    quantity = request.quantity
    if quantity is None:
        quantity = _contracts_from_notional(request, rules=rules)
    size = _round_down_to_step(quantity, rules.lot_size)
    if size <= 0:
        raise ValueError("OKX SWAP size rounds to zero")
    if size < rules.min_size:
        raise ValueError("OKX SWAP size is below minSz after normalization")
    return size


def _contracts_from_notional(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> Decimal:
    if request.notional is None:
        raise ValueError("OKX SWAP orders require contract quantity or notional")
    price = (
        request.limit_price
        if request.order_type in {"limit", "stop_limit"} and request.limit_price is not None
        else request.price_reference
    )
    if price is None:
        raise ValueError("OKX SWAP notional conversion requires priceReference")
    if price <= 0:
        raise ValueError("OKX SWAP priceReference must be positive")
    return request.notional / (price * rules.contract_value)


def _normalized_price(
    value: Decimal,
    *,
    side: str,
    rules: OkxSwapInstrumentRules,
) -> Decimal:
    return _round_to_tick(value, tick_size=rules.tick_size, side=side)


def _public_normalized_order(params: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "instId": params["instId"],
        "ordType": params["ordType"],
        "posSide": params["posSide"],
        "side": params["side"],
        "sz": params["sz"],
        "tdMode": params["tdMode"],
    }
    for key in ("px", "reduceOnly"):
        if key in params:
            payload[key] = params[key]
    attach_algo_orders = params.get("attachAlgoOrds")
    if isinstance(attach_algo_orders, list):
        stop_loss = _public_attached_stop_loss(attach_algo_orders)
        if stop_loss is not None:
            payload["attachedStopLoss"] = stop_loss
    reject_secret_fields(payload)
    return payload


def _okx_client_order_id(client_order_id: str) -> str:
    digest = hashlib.sha256(client_order_id.encode()).hexdigest()
    return f"ytm{digest[:29]}"


def _okx_attached_algo_client_order_id(client_order_id: str, suffix: str) -> str:
    digest = hashlib.sha256(f"{client_order_id}:{suffix}".encode()).hexdigest()
    return f"ytm{suffix}{digest[:27]}"


def _okx_attached_stop_loss(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, str]:
    if request.stop_loss is None:
        raise ValueError("OKX attached stop-loss requires stopLoss")
    trigger_price = _normalized_stop_loss_trigger_price(
        request.stop_loss,
        request=request,
        rules=rules,
    )
    _validate_stop_loss_side(trigger_price, request=request)
    return {
        "attachAlgoClOrdId": _okx_attached_algo_client_order_id(
            request.client_order_id,
            "sl",
        ),
        "slOrdPx": "-1",
        "slTriggerPx": _decimal_text(trigger_price),
        "slTriggerPxType": "last",
    }


def _public_attached_stop_loss(items: list[object]) -> dict[str, str] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        trigger_price = _optional_text(item.get("slTriggerPx"))
        order_price = _optional_text(item.get("slOrdPx"))
        if trigger_price is None or order_price is None:
            continue
        payload = {
            "attachAlgoClOrdId": _optional_text(item.get("attachAlgoClOrdId")) or "",
            "orderPrice": "market" if order_price == "-1" else order_price,
            "slOrdPx": order_price,
            "slTriggerPx": trigger_price,
            "slTriggerPxType": _optional_text(item.get("slTriggerPxType")) or "last",
        }
        return {key: value for key, value in payload.items() if value}
    return None


def _normalized_stop_loss_trigger_price(
    value: Decimal,
    *,
    request: BrokerOrderRequest,
    rules: OkxSwapInstrumentRules,
) -> Decimal:
    if request.side == "long":
        return _round_to_tick(value, tick_size=rules.tick_size, side="sell")
    if request.side == "short":
        return _round_to_tick(value, tick_size=rules.tick_size, side="buy")
    raise ValueError("OKX attached stop-loss side is unsupported")


def _validate_stop_loss_side(value: Decimal, *, request: BrokerOrderRequest) -> None:
    reference = request.limit_price if request.limit_price is not None else request.price_reference
    if reference is None:
        return
    if request.side == "long" and value >= reference:
        raise ValueError("OKX long stopLoss must be below entry price")
    if request.side == "short" and value <= reference:
        raise ValueError("OKX short stopLoss must be above entry price")


def _okx_order_type(order_type: str) -> str:
    mapping = {
        "limit": "limit",
        "market": "market",
    }
    try:
        return mapping[order_type]
    except KeyError as exc:
        raise ValueError("OKX SWAP order type is unsupported") from exc


def _okx_side(request: BrokerOrderRequest) -> str:
    if request.side == "long" and request.position_effect == "open":
        return "buy"
    if request.side == "long" and request.position_effect in {"reduce", "close"}:
        return "sell"
    if request.side == "short" and request.position_effect == "open":
        return "sell"
    if request.side == "short" and request.position_effect in {"reduce", "close"}:
        return "buy"
    raise ValueError("OKX SWAP side/effect is unsupported")


def _okx_td_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"cross", "isolated"}:
        raise ValueError("OKX SWAP tdMode must be cross or isolated")
    return normalized


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _round_to_tick(value: Decimal, *, tick_size: Decimal, side: str) -> Decimal:
    rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
    return (value / tick_size).to_integral_value(rounding=rounding) * tick_size


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _integer_leverage_text(value: Decimal) -> str:
    leverage = max(Decimal("1"), value.to_integral_value(rounding=ROUND_CEILING))
    return format(leverage, "f")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"OKX {field_name} is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _required_positive_decimal(value: object, field_name: str) -> Decimal:
    decimal = _decimal(value, field_name)
    if decimal <= 0:
        raise ValueError(f"OKX {field_name} must be positive")
    return decimal


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value in (None, ""):
        return None
    return _decimal(value, field_name)


def _optional_abs_decimal(value: object, field_name: str) -> Decimal | None:
    decimal = _optional_decimal(value, field_name)
    return abs(decimal) if decimal is not None else None


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"OKX {field_name} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"OKX {field_name} must be numeric") from exc
    if not decimal.is_finite():
        raise ValueError(f"OKX {field_name} must be finite")
    return decimal
