"""Binance USD-M Futures mainnet validate-only adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from ytm_executor.adapters import BrokerAdapterResult, BrokerOrderRequest
from ytm_executor.guards import reject_secret_fields

BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER = "binance_usdm_futures_mainnet_order_test"


class BinanceFuturesRestApi(Protocol):
    def test_order(self, **kwargs: Any) -> Any: ...


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
        params = binance_usdm_futures_order_test_params(request)
        api.test_order(**params)
        payload = {
            "adapter": BINANCE_USDM_FUTURES_MAINNET_ORDER_TEST_ADAPTER,
            "clientOrderId": request.client_order_id,
            "executorAction": "order_test_validated",
            "mainnet": True,
            "market": "usdm_futures",
            "provider": "binance",
        }
        reject_secret_fields(payload)
        return BrokerAdapterResult(status="acknowledged", payload=payload)


def binance_usdm_futures_order_test_params(request: BrokerOrderRequest) -> dict[str, Any]:
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
        TestOrderPositionSideEnum,
        TestOrderSideEnum,
        TestOrderTimeInForceEnum,
    )

    side = _binance_side(request)
    params: dict[str, Any] = {
        "new_client_order_id": request.client_order_id,
        "position_side": TestOrderPositionSideEnum.BOTH,
        "side": TestOrderSideEnum(side),
        "symbol": request.symbol,
        "type": _binance_order_type(request.order_type),
    }
    if request.position_effect in {"reduce", "close"}:
        params["reduce_only"] = "true"

    if request.order_type in {"limit", "stop_limit"}:
        if request.quantity is None:
            raise ValueError("Binance Futures limit orders require quantity")
        if request.limit_price is None:
            raise ValueError("Binance Futures limit orders require price")
        params["quantity"] = _float(request.quantity)
        params["price"] = _float(request.limit_price)
        params["time_in_force"] = TestOrderTimeInForceEnum(request.time_in_force or "GTC")
    elif request.order_type == "market":
        if request.quantity is None:
            raise ValueError("Binance Futures market orders require quantity")
        params["quantity"] = _float(request.quantity)
    elif request.order_type in {"stop", "stop_market"}:
        if request.quantity is None:
            raise ValueError("Binance Futures stop-market orders require quantity")
        if request.stop_price is None:
            raise ValueError("Binance Futures stop-market orders require stop price")
        params["quantity"] = _float(request.quantity)
        params["stop_price"] = _float(request.stop_price)
    reject_secret_fields(params)
    return params


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


def _float(value: Decimal) -> float:
    return float(value)
