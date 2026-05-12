"""Broker adapter boundary and order request normalization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from ytm_executor.guards import reject_secret_fields

SUPPORTED_POSITION_EFFECTS = frozenset({"open", "reduce", "close"})
SUPPORTED_SIDES = frozenset({"long", "short"})
SUPPORTED_ORDER_TYPES = frozenset({"limit", "market", "stop", "stop_limit", "stop_market"})


class BrokerAdapter(Protocol):
    provider: str

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult: ...


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    provider: str
    execution_mode: str
    symbol: str
    side: str
    position_effect: str
    order_type: str
    client_order_id: str
    quantity: Decimal | None
    notional: Decimal | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    stop_loss: Decimal | None
    price_reference: Decimal | None
    time_in_force: str | None
    market: str | None = None
    margin_mode: str | None = None
    leverage: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BrokerAdapterResult:
    status: str
    payload: dict[str, Any]


class DisabledBrokerAdapter:
    """Adapter implementation used before real broker placement is enabled."""

    def __init__(self, *, provider: str) -> None:
        self.provider = provider

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult:
        payload = {
            "adapter": "disabled",
            "clientOrderId": request.client_order_id,
            "executorAction": "order_placement_skipped",
            "orderRequest": order_request_public_payload(request),
            "reason": "broker adapters are not enabled in this foundation build",
        }
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


def build_order_request(command: dict[str, Any], *, execution_mode: str) -> BrokerOrderRequest:
    reject_secret_fields(command)
    payload = _command_payload(command)
    provider = _required_text(command.get("provider"), "provider")
    symbol = _required_text(
        _first(command, payload, ("symbol", "instrumentId", "instrument", "figi", "ticker")),
        "symbol",
    ).upper()
    side = _required_text(_first(command, payload, ("side", "direction")), "side").lower()
    if side not in SUPPORTED_SIDES:
        raise ValueError("side is unsupported")
    position_effect = _required_text(
        _first(command, payload, ("positionEffect", "effect")),
        "positionEffect",
    ).lower()
    if position_effect not in SUPPORTED_POSITION_EFFECTS:
        raise ValueError("positionEffect is unsupported")
    order_type = _normalized_order_type(_first(command, payload, ("orderType", "type")))
    if order_type not in SUPPORTED_ORDER_TYPES:
        raise ValueError("orderType is unsupported")

    request = BrokerOrderRequest(
        provider=provider,
        execution_mode=execution_mode,
        symbol=symbol,
        side=side,
        position_effect=position_effect,
        order_type=order_type,
        client_order_id=deterministic_client_order_id(command),
        quantity=_optional_decimal(_first(command, payload, ("quantity", "qty", "baseQuantity"))),
        notional=_optional_decimal(
            _first(command, payload, ("orderNotional", "notional", "quoteQuantity"))
        ),
        limit_price=_optional_decimal(_first(command, payload, ("price", "limitPrice"))),
        stop_price=_optional_decimal(_first(command, payload, ("stopPrice", "triggerPrice"))),
        stop_loss=_optional_decimal(
            _first(command, payload, ("stopLoss", "stopLossTriggerPrice", "slTriggerPx"))
        ),
        price_reference=_optional_decimal(
            _first(command, payload, ("priceReference", "entryPriceReference", "markPrice"))
        ),
        time_in_force=_optional_text(_first(command, payload, ("timeInForce",))),
        market=_optional_text(
            _first(command, payload, ("market", "brokerMarket", "exchangeMarket")),
        ),
        margin_mode=_optional_text(_first(command, payload, ("marginMode", "mgnMode", "tdMode"))),
        leverage=_optional_decimal(_first(command, payload, ("leverage",))),
    )
    _validate_order_request(request)
    return request


def deterministic_client_order_id(command: dict[str, Any]) -> str:
    existing = _optional_text(command.get("clientOrderId"))
    if existing:
        return existing
    command_id = _required_text(command.get("id"), "id")
    provider = _optional_text(command.get("provider")) or "unknown"
    digest = hashlib.sha256(f"{provider}:{command_id}".encode()).hexdigest()[:32]
    return f"ytm_{digest}"


def order_request_public_payload(request: BrokerOrderRequest) -> dict[str, Any]:
    payload = {
        "clientOrderId": request.client_order_id,
        "executionMode": request.execution_mode,
        "orderType": request.order_type,
        "positionEffect": request.position_effect,
        "provider": request.provider,
        "side": request.side,
        "symbol": request.symbol,
    }
    if request.quantity is not None:
        payload["quantity"] = _decimal_text(request.quantity)
    if request.notional is not None:
        payload["notional"] = _decimal_text(request.notional)
    if request.limit_price is not None:
        payload["limitPrice"] = _decimal_text(request.limit_price)
    if request.stop_price is not None:
        payload["stopPrice"] = _decimal_text(request.stop_price)
    if request.stop_loss is not None:
        payload["stopLoss"] = _decimal_text(request.stop_loss)
    if request.price_reference is not None:
        payload["priceReference"] = _decimal_text(request.price_reference)
    if request.time_in_force:
        payload["timeInForce"] = request.time_in_force
    if request.market:
        payload["market"] = request.market
    if request.margin_mode:
        payload["marginMode"] = request.margin_mode
    if request.leverage is not None:
        payload["leverage"] = _decimal_text(request.leverage)
    reject_secret_fields(payload)
    return payload


def _validate_order_request(request: BrokerOrderRequest) -> None:
    if request.order_type in {"limit", "stop_limit"} and request.limit_price is None:
        raise ValueError("limit order price is required")
    if request.order_type in {"stop", "stop_limit", "stop_market"} and request.stop_price is None:
        raise ValueError("stop order trigger price is required")
    if request.quantity is None and request.notional is None:
        raise ValueError("quantity or notional is required")


def _command_payload(command: dict[str, Any]) -> dict[str, Any]:
    payload = command.get("commandPayload")
    return payload if isinstance(payload, dict) else {}


def _first(command: dict[str, Any], payload: dict[str, Any], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in command:
            return command[key]
        if key in payload:
            return payload[key]
    return None


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_order_type(value: object) -> str:
    return str(value).strip().lower().replace("-", "_") if isinstance(value, str) else ""


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("numeric fields must not be boolean")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric fields must be numeric") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("numeric fields must be finite and positive")
    return decimal


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
