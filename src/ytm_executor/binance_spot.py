"""Binance Spot testnet adapter using the official Binance Python connector."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from ytm_executor.adapters import BrokerAdapterResult, BrokerOrderRequest
from ytm_executor.guards import reject_secret_fields

BINANCE_SPOT_TESTNET_ORDER_TEST_ADAPTER = "binance_spot_testnet_order_test"


class BinanceRestApi(Protocol):
    def order_test(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class BinanceSpotTestnetOrderTestAdapter:
    """Validate Binance Spot testnet orders without placing them."""

    api_key: str
    api_secret: str
    rest_api: BinanceRestApi | None = None

    provider = "binance"

    def prepare_order(self, request: BrokerOrderRequest) -> BrokerAdapterResult:
        if request.provider != "binance":
            raise ValueError("Binance adapter received a non-Binance request")
        api = self.rest_api or _build_testnet_rest_api(
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        params = binance_order_test_params(request)
        api.order_test(**params)
        payload = {
            "adapter": BINANCE_SPOT_TESTNET_ORDER_TEST_ADAPTER,
            "clientOrderId": request.client_order_id,
            "executorAction": "order_test_validated",
            "provider": "binance",
            "testnet": True,
        }
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


def binance_order_test_params(request: BrokerOrderRequest) -> dict[str, Any]:
    side = _binance_side(request)
    order_type = _binance_order_type(request.order_type)
    params: dict[str, Any] = {
        "new_client_order_id": request.client_order_id,
        "side": side,
        "symbol": request.symbol,
        "type": order_type,
    }
    if request.order_type in {"limit", "stop_limit"}:
        if request.quantity is None:
            raise ValueError("Binance limit orders require quantity")
        if request.limit_price is None:
            raise ValueError("Binance limit orders require price")
        params["quantity"] = _float(request.quantity)
        params["price"] = _float(request.limit_price)
        params["time_in_force"] = request.time_in_force or "GTC"
    elif request.order_type == "market":
        if request.quantity is not None:
            params["quantity"] = _float(request.quantity)
        elif request.notional is not None:
            params["quote_order_qty"] = _float(request.notional)
        else:
            raise ValueError("Binance market orders require quantity or notional")
    elif request.order_type in {"stop", "stop_market"}:
        if request.quantity is None:
            raise ValueError("Binance stop orders require quantity")
        if request.stop_price is None:
            raise ValueError("Binance stop orders require stop price")
        params["quantity"] = _float(request.quantity)
        params["stop_price"] = _float(request.stop_price)
    reject_secret_fields(params)
    return params


def _build_testnet_rest_api(*, api_key: str, api_secret: str) -> BinanceRestApi:
    from binance_common.configuration import ConfigurationRestAPI
    from binance_common.constants import SPOT_REST_API_TESTNET_URL
    from binance_sdk_spot.spot import Spot

    configuration = ConfigurationRestAPI(
        api_key=api_key,
        api_secret=api_secret,
        base_path=SPOT_REST_API_TESTNET_URL,
    )
    return Spot(config_rest_api=configuration).rest_api


def _binance_side(request: BrokerOrderRequest) -> str:
    if request.side == "long" and request.position_effect == "open":
        return "BUY"
    if request.side == "long" and request.position_effect in {"reduce", "close"}:
        return "SELL"
    raise ValueError("Binance Spot adapter supports long spot open/reduce/close only")


def _binance_order_type(order_type: str) -> str:
    mapping = {
        "limit": "LIMIT",
        "market": "MARKET",
        "stop": "STOP_LOSS",
        "stop_limit": "STOP_LOSS_LIMIT",
        "stop_market": "STOP_LOSS",
    }
    try:
        return mapping[order_type]
    except KeyError as exc:
        raise ValueError("Binance order type is unsupported") from exc


def _float(value: Decimal) -> float:
    return float(value)
