"""OKX SWAP mainnet validate-only adapter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Protocol

from ytm_executor.adapters import BrokerAdapterResult, BrokerOrderRequest
from ytm_executor.guards import reject_secret_fields

OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER = "okx_swap_mainnet_order_precheck"
OKX_ORDER_PRECHECK_PATH = "/api/v5/trade/order-precheck"


class OkxSwapApi(Protocol):
    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, Any]: ...
    def order_precheck(self, params: dict[str, str]) -> dict[str, Any]: ...


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
        if request.execution_mode != "real":
            raise ValueError("OKX SWAP order precheck requires executionMode=real")
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
class OkxSdkSwapApi:
    account_api: Any
    trade_api: Any

    def get_instruments(self, *, inst_type: str, inst_id: str) -> dict[str, Any]:
        return self.account_api.get_instruments(instType=inst_type, instId=inst_id)

    def order_precheck(self, params: dict[str, str]) -> dict[str, Any]:
        return self.trade_api._request_with_params("POST", OKX_ORDER_PRECHECK_PATH, params)


def okx_swap_order_precheck_params(
    request: BrokerOrderRequest,
    *,
    rules: OkxSwapInstrumentRules,
) -> dict[str, str]:
    order_type = _okx_order_type(request.order_type)
    side = _okx_side(request)
    size = _normalized_size(request, rules=rules)
    params = {
        "clOrdId": _okx_client_order_id(request.client_order_id),
        "instId": request.symbol,
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
    reject_secret_fields(params)
    return params


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


def _public_normalized_order(params: dict[str, str]) -> dict[str, str]:
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
    reject_secret_fields(payload)
    return payload


def _okx_client_order_id(client_order_id: str) -> str:
    digest = hashlib.sha256(client_order_id.encode()).hexdigest()
    return f"ytm{digest[:29]}"


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


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"OKX {field_name} is required")
    return value.strip()


def _required_positive_decimal(value: object, field_name: str) -> Decimal:
    decimal = _decimal(value, field_name)
    if decimal <= 0:
        raise ValueError(f"OKX {field_name} must be positive")
    return decimal


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
