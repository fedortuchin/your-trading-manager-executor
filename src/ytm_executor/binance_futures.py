"""Binance USD-M Futures mainnet validate-only adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Protocol

from ytm_executor.adapters import BrokerAdapterResult, BrokerOrderRequest
from ytm_executor.guards import reject_secret_fields

BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER = "binance_usdm_futures_mainnet_order_test"


class BinanceFuturesRestApi(Protocol):
    def exchange_information(self) -> Any: ...
    def test_order(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class BinanceUsdmFuturesSymbolRules:
    symbol: str
    status: str
    order_types: frozenset[str]
    time_in_force: frozenset[str]
    min_price: Decimal | None
    max_price: Decimal | None
    tick_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal
    market_min_qty: Decimal | None
    market_max_qty: Decimal | None
    market_step_size: Decimal | None
    min_notional: Decimal | None


@dataclass(frozen=True, slots=True)
class BinanceUsdmFuturesMainnetOrderTestAdapter:
    """Validate Binance USD-M Futures mainnet orders without placing them."""

    api_key: str
    api_secret: str
    rest_api: BinanceFuturesRestApi | None = None

    provider = "binance"

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult:
        if request.provider != "binance":
            raise ValueError("Binance adapter received a non-Binance request")
        if request.execution_mode != "real":
            raise ValueError("Binance Futures mainnet order test requires executionMode=real")
        api = self.rest_api or _build_mainnet_rest_api(
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        rules = binance_usdm_futures_symbol_rules(
            _exchange_information_payload(api.exchange_information()),
            request.symbol,
        )
        params = binance_usdm_futures_order_test_params(request, rules=rules)
        api.test_order(**params)
        payload = {
            "adapter": BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
            "clientOrderId": request.client_order_id,
            "executorAction": "order_test_validated",
            "mainnet": True,
            "market": "usdm_futures",
            "normalizedOrder": _public_normalized_order(params),
            "provider": "binance",
        }
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


def binance_usdm_futures_order_test_params(
    request: BrokerOrderRequest,
    *,
    rules: BinanceUsdmFuturesSymbolRules | None = None,
) -> dict[str, Any]:
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
        TestOrderPositionSideEnum,
        TestOrderSideEnum,
        TestOrderTimeInForceEnum,
    )

    side = _binance_side(request)
    order_type = _binance_order_type(request.order_type)
    if rules is not None:
        _validate_rules_compatibility(request=request, order_type=order_type, rules=rules)
    quantity = _normalized_quantity(request, rules=rules)
    price = _normalized_price(request.limit_price, side=side, rules=rules)
    stop_price = _normalized_price(request.stop_price, side=side, rules=rules)
    _validate_min_notional(request=request, price=price, quantity=quantity, rules=rules)
    params: dict[str, Any] = {
        "new_client_order_id": request.client_order_id,
        "position_side": TestOrderPositionSideEnum.BOTH,
        "side": TestOrderSideEnum(side),
        "symbol": request.symbol,
        "type": order_type,
    }
    if request.position_effect in {"reduce", "close"}:
        params["reduce_only"] = "true"

    if request.order_type in {"limit", "stop_limit"}:
        if quantity is None:
            raise ValueError("Binance Futures limit orders require quantity")
        if price is None:
            raise ValueError("Binance Futures limit orders require price")
        params["quantity"] = _float(quantity)
        params["price"] = _float(price)
        params["time_in_force"] = TestOrderTimeInForceEnum(_time_in_force(request))
        if request.order_type == "stop_limit":
            if stop_price is None:
                raise ValueError("Binance Futures stop-limit orders require stop price")
            params["stop_price"] = _float(stop_price)
    elif request.order_type == "market":
        if quantity is None:
            raise ValueError("Binance Futures market orders require quantity")
        params["quantity"] = _float(quantity)
    elif request.order_type in {"stop", "stop_market"}:
        if quantity is None:
            raise ValueError("Binance Futures stop-market orders require quantity")
        if stop_price is None:
            raise ValueError("Binance Futures stop-market orders require stop price")
        params["quantity"] = _float(quantity)
        params["stop_price"] = _float(stop_price)
    reject_secret_fields(params)
    return params


def binance_usdm_futures_symbol_rules(
    exchange_info: dict[str, Any],
    symbol: str,
) -> BinanceUsdmFuturesSymbolRules:
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("Binance exchangeInfo symbols are missing")
    normalized_symbol = symbol.upper()
    for item in symbols:
        if isinstance(item, dict) and str(item.get("symbol") or "").upper() == normalized_symbol:
            return _symbol_rules_from_payload(item)
    raise ValueError(f"Binance Futures symbol is not found in exchangeInfo: {normalized_symbol}")


def _build_mainnet_rest_api(*, api_key: str, api_secret: str) -> BinanceFuturesRestApi:
    from binance_common.configuration import ConfigurationRestAPI
    from binance_common.constants import DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL
    from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
        DerivativesTradingUsdsFutures,
    )

    configuration = ConfigurationRestAPI(
        api_key=api_key,
        api_secret=api_secret,
        base_path=DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL,
    )
    return DerivativesTradingUsdsFutures(config_rest_api=configuration).rest_api


def _symbol_rules_from_payload(payload: dict[str, Any]) -> BinanceUsdmFuturesSymbolRules:
    filters = payload.get("filters")
    if not isinstance(filters, list):
        raise ValueError("Binance symbol filters are missing")
    price_filter = _filter(filters, "PRICE_FILTER")
    lot_size = _filter(filters, "LOT_SIZE")
    market_lot_size = _filter(filters, "MARKET_LOT_SIZE")
    min_notional = _filter(filters, "MIN_NOTIONAL")
    return BinanceUsdmFuturesSymbolRules(
        symbol=_required_text(payload.get("symbol"), "symbol").upper(),
        status=_required_text(payload.get("status"), "status").upper(),
        order_types=frozenset(_string_list(payload.get("orderTypes"), "orderTypes")),
        time_in_force=frozenset(_string_list(payload.get("timeInForce"), "timeInForce")),
        min_price=_optional_decimal(price_filter.get("minPrice"), "minPrice"),
        max_price=_optional_decimal(price_filter.get("maxPrice"), "maxPrice"),
        tick_size=_required_positive_decimal(price_filter.get("tickSize"), "tickSize"),
        min_qty=_required_positive_decimal(lot_size.get("minQty"), "minQty"),
        max_qty=_required_positive_decimal(lot_size.get("maxQty"), "maxQty"),
        step_size=_required_positive_decimal(lot_size.get("stepSize"), "stepSize"),
        market_min_qty=_optional_decimal(market_lot_size.get("minQty"), "marketMinQty"),
        market_max_qty=_optional_decimal(market_lot_size.get("maxQty"), "marketMaxQty"),
        market_step_size=_optional_decimal(market_lot_size.get("stepSize"), "marketStepSize"),
        min_notional=_optional_decimal(min_notional.get("notional"), "minNotional"),
    )


def _exchange_information_payload(response: Any) -> dict[str, Any]:
    if hasattr(response, "data") and callable(response.data):
        response = response.data()
    if hasattr(response, "to_dict") and callable(response.to_dict):
        response = response.to_dict()
    if not isinstance(response, dict):
        raise ValueError("Binance exchangeInfo response is invalid")
    reject_secret_fields(response)
    return response


def _validate_rules_compatibility(
    *,
    request: BrokerOrderRequest,
    order_type: str,
    rules: BinanceUsdmFuturesSymbolRules,
) -> None:
    if rules.status != "TRADING":
        raise ValueError("Binance Futures symbol is not trading")
    if order_type not in rules.order_types:
        raise ValueError("Binance Futures order type is not allowed for symbol")
    if request.time_in_force and _time_in_force(request) not in rules.time_in_force:
        raise ValueError("Binance Futures timeInForce is not allowed for symbol")


def _normalized_quantity(
    request: BrokerOrderRequest,
    *,
    rules: BinanceUsdmFuturesSymbolRules | None,
) -> Decimal | None:
    if request.quantity is None:
        return None
    if rules is None:
        return request.quantity
    min_qty = rules.min_qty
    max_qty = rules.max_qty
    step_size = rules.step_size
    if request.order_type == "market":
        min_qty = rules.market_min_qty or min_qty
        max_qty = rules.market_max_qty or max_qty
        step_size = rules.market_step_size or step_size
    quantity = _round_down_to_step(request.quantity, step_size)
    if quantity <= 0:
        raise ValueError("Binance Futures quantity rounds to zero")
    if quantity < min_qty:
        raise ValueError("Binance Futures quantity is below minQty after normalization")
    if quantity > max_qty:
        raise ValueError("Binance Futures quantity exceeds maxQty")
    return quantity


def _normalized_price(
    value: Decimal | None,
    *,
    side: str,
    rules: BinanceUsdmFuturesSymbolRules | None,
) -> Decimal | None:
    if value is None:
        return None
    if rules is None:
        return value
    price = _round_to_tick(value, tick_size=rules.tick_size, side=side)
    if rules.min_price is not None and price < rules.min_price:
        raise ValueError("Binance Futures price is below minPrice after normalization")
    if rules.max_price is not None and price > rules.max_price:
        raise ValueError("Binance Futures price exceeds maxPrice after normalization")
    return price


def _validate_min_notional(
    *,
    request: BrokerOrderRequest,
    price: Decimal | None,
    quantity: Decimal | None,
    rules: BinanceUsdmFuturesSymbolRules | None,
) -> None:
    if rules is None or rules.min_notional is None:
        return
    if price is not None and quantity is not None:
        notional = price * quantity
    elif request.notional is not None:
        notional = request.notional
    else:
        raise ValueError("Binance Futures order notional is required for minNotional preflight")
    if notional < rules.min_notional:
        raise ValueError("Binance Futures order notional is below minNotional")


def _public_normalized_order(params: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": params.get("symbol"),
        "side": _enum_value(params.get("side")),
        "type": params.get("type"),
        "positionSide": _enum_value(params.get("position_side")),
    }
    for source, target in (
        ("quantity", "quantity"),
        ("price", "price"),
        ("stop_price", "stopPrice"),
        ("time_in_force", "timeInForce"),
        ("reduce_only", "reduceOnly"),
    ):
        if source in params:
            payload[target] = _enum_value(params[source])
    reject_secret_fields(payload)
    return payload


def _binance_side(request: BrokerOrderRequest) -> str:
    if request.side == "long" and request.position_effect == "open":
        return "BUY"
    if request.side == "long" and request.position_effect in {"reduce", "close"}:
        return "SELL"
    if request.side == "short" and request.position_effect == "open":
        return "SELL"
    if request.side == "short" and request.position_effect in {"reduce", "close"}:
        return "BUY"
    raise ValueError("Binance Futures side/effect is unsupported")


def _binance_order_type(order_type: str) -> str:
    mapping = {
        "limit": "LIMIT",
        "market": "MARKET",
        "stop": "STOP_MARKET",
        "stop_limit": "STOP",
        "stop_market": "STOP_MARKET",
    }
    try:
        return mapping[order_type]
    except KeyError as exc:
        raise ValueError("Binance Futures order type is unsupported") from exc


def _time_in_force(request: BrokerOrderRequest) -> str:
    return (request.time_in_force or "GTC").strip().upper()


def _float(value: Decimal) -> float:
    return float(value)


def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _round_to_tick(value: Decimal, *, tick_size: Decimal, side: str) -> Decimal:
    rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
    return (value / tick_size).to_integral_value(rounding=rounding) * tick_size


def _filter(filters: list[object], filter_type: str) -> dict[str, Any]:
    for item in filters:
        if isinstance(item, dict) and item.get("filterType") == filter_type:
            return item
    raise ValueError(f"Binance symbol filter is missing: {filter_type}")


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Binance {field_name} must be a string array")
    return [item.strip().upper() for item in value if item.strip()]


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Binance {field_name} is required")
    return value.strip()


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value, field_name)


def _required_positive_decimal(value: object, field_name: str) -> Decimal:
    decimal = _decimal(value, field_name)
    if decimal <= 0:
        raise ValueError(f"Binance {field_name} must be positive")
    return decimal


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"Binance {field_name} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Binance {field_name} must be numeric") from exc
    if not decimal.is_finite():
        raise ValueError(f"Binance {field_name} must be finite")
    return decimal


def _enum_value(value: object) -> object:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return value
