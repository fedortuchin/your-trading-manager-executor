"""Provider reconciliation snapshot capture from the executor host."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ytm_executor.guards import reject_secret_fields
from ytm_executor.okx_swap import _okx_response_data, okx_swap_instrument_id

OKX_RECONCILIATION_ALGO_ORDER_TYPES = ("conditional", "oco", "trigger", "move_order_stop")
YTM_OKX_CLIENT_ORDER_PREFIX = "ytm"


class OkxReconciliationApi(Protocol):
    def get_account_balance(self, ccy: str = "") -> dict[str, Any]: ...
    def get_positions(
        self,
        inst_type: str = "",
        inst_id: str = "",
        pos_id: str = "",
    ) -> dict[str, Any]: ...
    def get_order_list(self, inst_type: str = "", state: str = "") -> dict[str, Any]: ...
    def get_orders_history(
        self,
        inst_type: str,
        state: str = "",
        limit: str = "",
    ) -> dict[str, Any]: ...
    def get_fills_history(
        self,
        inst_type: str,
        limit: str = "",
    ) -> dict[str, Any]: ...
    def order_algos_list(
        self,
        ord_type: str = "",
        inst_type: str = "",
        limit: str = "",
    ) -> dict[str, Any]: ...
    def order_algos_history(
        self,
        ord_type: str,
        state: str = "",
        inst_type: str = "",
        limit: str = "",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class OkxSwapReconciliationAdapter:
    api_key: str
    api_secret: str
    passphrase: str
    api: OkxReconciliationApi | None = None

    def capture_snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        api = self.api or _build_mainnet_reconciliation_api(
            api_key=self.api_key,
            api_secret=self.api_secret,
            passphrase=self.passphrase,
        )
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        balances = _okx_response_data(api.get_account_balance(), "account_balance")
        positions = _okx_response_data(
            api.get_positions(inst_type="SWAP"),
            "positions",
        )
        orders = _okx_response_data(
            api.get_order_list(inst_type="SWAP"),
            "orders_pending",
        )
        order_history = _okx_response_data(
            api.get_orders_history(inst_type="SWAP", limit="100"),
            "orders_history",
        )
        warnings: list[str] = []
        algo_orders = _capture_algo_orders(api, warnings=warnings)
        fills = _okx_response_data(
            api.get_fills_history(inst_type="SWAP", limit="100"),
            "fills_history",
        )
        close_sources = _close_sources_by_order_ref((*orders, *order_history, *algo_orders))
        snapshot = {
            "algoOrders": _sanitize_algo_orders(algo_orders),
            "balances": _sanitize_balances(balances),
            "capturedAt": _iso_z(captured_at),
            "fills": _sanitize_fills(fills, close_sources=close_sources),
            "market": "okx_swap",
            "orderHistory": _sanitize_orders(order_history),
            "openOrders": _sanitize_orders(orders),
            "positions": _sanitize_positions(positions),
            "provider": "okx",
            "source": "executor_okx_reconciliation",
            "zeroSecret": True,
        }
        if warnings:
            snapshot["warnings"] = warnings
        reject_secret_fields(snapshot)
        return snapshot


@dataclass(frozen=True, slots=True)
class OkxSdkReconciliationApi:
    account_api: Any
    trade_api: Any

    def get_account_balance(self, ccy: str = "") -> dict[str, Any]:
        return self.account_api.get_account_balance(ccy=ccy)

    def get_positions(
        self,
        inst_type: str = "",
        inst_id: str = "",
        pos_id: str = "",
    ) -> dict[str, Any]:
        return self.account_api.get_positions(
            instType=inst_type,
            instId=inst_id,
            posId=pos_id,
        )

    def get_order_list(self, inst_type: str = "", state: str = "") -> dict[str, Any]:
        return self.trade_api.get_order_list(instType=inst_type, state=state)

    def get_orders_history(
        self,
        inst_type: str,
        state: str = "",
        limit: str = "",
    ) -> dict[str, Any]:
        return self.trade_api.get_orders_history(instType=inst_type, state=state, limit=limit)

    def get_fills_history(
        self,
        inst_type: str,
        limit: str = "",
    ) -> dict[str, Any]:
        return self.trade_api.get_fills_history(instType=inst_type, limit=limit)

    def order_algos_list(
        self,
        ord_type: str = "",
        inst_type: str = "",
        limit: str = "",
    ) -> dict[str, Any]:
        return self.trade_api.order_algos_list(
            ordType=ord_type,
            instType=inst_type,
            limit=limit,
        )

    def order_algos_history(
        self,
        ord_type: str,
        state: str = "",
        inst_type: str = "",
        limit: str = "",
    ) -> dict[str, Any]:
        return self.trade_api.order_algos_history(
            ordType=ord_type,
            state=state,
            instType=inst_type,
            limit=limit,
        )


def _build_mainnet_reconciliation_api(
    *,
    api_key: str,
    api_secret: str,
    passphrase: str,
) -> OkxReconciliationApi:
    import okx.Account as Account
    import okx.Trade as Trade

    return OkxSdkReconciliationApi(
        account_api=Account.AccountAPI(api_key, api_secret, passphrase, False, "0"),
        trade_api=Trade.TradeAPI(api_key, api_secret, passphrase, False, "0"),
    )


def _sanitize_balances(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    balances: list[dict[str, str]] = []
    for account in items:
        details = account.get("details")
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, dict):
                continue
            balances.append(
                _compact(
                    {
                        "availableBalance": _text(detail.get("availBal")),
                        "cashBalance": _text(detail.get("cashBal")),
                        "currency": _text(detail.get("ccy")),
                        "equity": _text(detail.get("eq")),
                        "frozenBalance": _text(detail.get("frozenBal")),
                        "isolatedEquity": _text(detail.get("isoEq")),
                        "usdEquity": _text(detail.get("eqUsd")),
                    }
                )
            )
    balances.sort(key=lambda item: item.get("currency", ""))
    reject_secret_fields(balances)
    return balances


def _sanitize_positions(items: list[dict[str, Any]]) -> list[dict[str, str | bool]]:
    positions: list[dict[str, str | bool]] = []
    for item in items:
        inst_id = _text(item.get("instId"))
        quantity = _text(item.get("pos"))
        positions.append(
            _compact(
                {
                    "averageEntryPrice": _text(item.get("avgPx")),
                    "instrumentId": inst_id,
                    "leverage": _text(item.get("lever")),
                    "margin": _text(item.get("margin")),
                    "marginMode": _text(item.get("mgnMode")),
                    "markPrice": _text(item.get("markPx")),
                    "notionalUsd": _text(item.get("notionalUsd")),
                    "positionId": _text(item.get("posId")),
                    "positionSide": _text(item.get("posSide")),
                    "quantity": quantity,
                    "side": _position_side(pos_side=_text(item.get("posSide")), quantity=quantity),
                    "symbol": _plain_swap_symbol(inst_id),
                    "unrealizedPnl": _text(item.get("upl")),
                }
            )
        )
    positions.sort(
        key=lambda item: (
            str(item.get("instrumentId", "")),
            str(item.get("positionSide", "")),
            str(item.get("positionId", "")),
        )
    )
    reject_secret_fields(positions)
    return positions


def _sanitize_orders(items: list[dict[str, Any]]) -> list[dict[str, str | bool]]:
    orders: list[dict[str, str | bool]] = []
    for item in items:
        inst_id = _text(item.get("instId"))
        close_source = _infer_close_source(item)
        orders.append(
            _compact(
                {
                    "actualSide": _text(item.get("actualSide")),
                    "algoClientOrderId": _text(item.get("algoClOrdId")),
                    "algoOrderId": _text(item.get("algoId")),
                    "averageFillPrice": _text(item.get("avgPx")),
                    "clientOrderId": _text(item.get("clOrdId")),
                    "closeSource": close_source,
                    "filledQuantity": _text(item.get("accFillSz")),
                    "instrumentId": inst_id,
                    "orderType": _text(item.get("ordType")),
                    "positionSide": _text(item.get("posSide")),
                    "price": _text(item.get("px")),
                    "providerOrderId": _text(item.get("ordId")),
                    "quantity": _text(item.get("sz")),
                    "reduceOnly": _bool_text(item.get("reduceOnly")),
                    "side": _text(item.get("side")),
                    "state": _text(item.get("state")),
                    "symbol": _plain_swap_symbol(inst_id),
                    "timeInForce": _text(item.get("tif")),
                }
            )
        )
    orders.sort(
        key=lambda item: (
            str(item.get("instrumentId", "")),
            str(item.get("providerOrderId", "")),
            str(item.get("clientOrderId", "")),
        )
    )
    reject_secret_fields(orders)
    return orders


def _sanitize_algo_orders(items: list[dict[str, Any]]) -> list[dict[str, str | bool]]:
    orders: list[dict[str, str | bool]] = []
    for item in items:
        inst_id = _text(item.get("instId"))
        orders.append(
            _compact(
                {
                    "actualSide": _text(item.get("actualSide")),
                    "algoClientOrderId": _text(item.get("algoClOrdId")),
                    "algoOrderId": _text(item.get("algoId")),
                    "closeSource": _infer_close_source(item),
                    "instrumentId": inst_id,
                    "linkedClientOrderId": _text(item.get("clOrdId")),
                    "linkedOrderId": _text(item.get("ordId")),
                    "orderPrice": _text(item.get("orderPx") or item.get("ordPx")),
                    "orderType": _text(item.get("ordType")),
                    "positionSide": _text(item.get("posSide")),
                    "side": _text(item.get("side")),
                    "state": _text(item.get("state")),
                    "stopLossOrderPrice": _text(item.get("slOrdPx")),
                    "stopLossTriggerPrice": _text(item.get("slTriggerPx")),
                    "symbol": _plain_swap_symbol(inst_id),
                    "takeProfitOrderPrice": _text(item.get("tpOrdPx")),
                    "takeProfitTriggerPrice": _text(item.get("tpTriggerPx")),
                    "triggerPrice": _text(item.get("triggerPx")),
                }
            )
        )
    orders.sort(
        key=lambda item: (
            str(item.get("instrumentId", "")),
            str(item.get("algoOrderId", "")),
            str(item.get("linkedOrderId", "")),
        )
    )
    reject_secret_fields(orders)
    return orders


def _sanitize_fills(
    items: list[dict[str, Any]],
    *,
    close_sources: dict[str, str],
) -> list[dict[str, str | bool]]:
    fills: list[dict[str, str | bool]] = []
    for item in items:
        inst_id = _text(item.get("instId"))
        close_source = _fill_close_source(item, close_sources=close_sources)
        fills.append(
            _compact(
                {
                    "actualSide": _text(item.get("actualSide")),
                    "clientOrderId": _text(item.get("clOrdId")),
                    "closeSource": close_source,
                    "execType": _text(item.get("execType")),
                    "feeAmount": _text(item.get("fee")),
                    "feeCurrency": _text(item.get("feeCcy")),
                    "fillId": _text(item.get("billId") or item.get("tradeId")),
                    "fillPrice": _text(item.get("fillPx")),
                    "fillQuantity": _text(item.get("fillSz")),
                    "fillTime": _text(item.get("fillTime") or item.get("ts")),
                    "instrumentId": inst_id,
                    "orderType": _text(item.get("ordType")),
                    "positionSide": _text(item.get("posSide")),
                    "providerOrderId": _text(item.get("ordId")),
                    "realizedPnl": _text(
                        item.get("fillPnl") or item.get("pnl") or item.get("realizedPnl")
                    ),
                    "side": _text(item.get("side")),
                    "symbol": _plain_swap_symbol(inst_id),
                }
            )
        )
    fills.sort(
        key=lambda item: (
            str(item.get("fillTime", "")),
            str(item.get("providerOrderId", "")),
            str(item.get("fillId", "")),
        )
    )
    reject_secret_fields(fills)
    return fills


def _capture_algo_orders(
    api: OkxReconciliationApi,
    *,
    warnings: list[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ord_type in OKX_RECONCILIATION_ALGO_ORDER_TYPES:
        items.extend(
            _optional_okx_response_data(
                api.order_algos_list,
                f"algo_orders_pending_{ord_type}",
                warnings=warnings,
                ord_type=ord_type,
                inst_type="SWAP",
                limit="100",
            )
        )
        items.extend(
            _optional_okx_response_data(
                api.order_algos_history,
                f"algo_orders_history_{ord_type}",
                warnings=warnings,
                ord_type=ord_type,
                inst_type="SWAP",
                limit="100",
            )
        )
    return items


def _optional_okx_response_data(
    request: Any,
    endpoint: str,
    *,
    warnings: list[str],
    **kwargs: str,
) -> list[dict[str, Any]]:
    try:
        return _okx_response_data(request(**kwargs), endpoint)
    except Exception:
        warnings.append(f"{endpoint}_unavailable")
        return []


def _close_sources_by_order_ref(items: tuple[dict[str, Any], ...]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for item in items:
        close_source = _infer_close_source(item)
        if not close_source:
            continue
        for key in ("ordId", "algoId", "clOrdId", "algoClOrdId"):
            value = _text(item.get(key))
            if value:
                sources[value] = close_source
    return sources


def _fill_close_source(item: dict[str, Any], *, close_sources: dict[str, str]) -> str:
    explicit = _infer_close_source(item)
    if explicit:
        return explicit
    for key in ("ordId", "clOrdId", "algoId", "algoClOrdId"):
        value = _text(item.get(key))
        if value in close_sources:
            return close_sources[value]
    return ""


def _infer_close_source(item: dict[str, Any]) -> str:
    for key in ("closeSource", "actualSide", "orderRole", "triggerSource"):
        value = _normalized_token(item.get(key))
        if value in {"tp", "takeprofit", "take_profit"}:
            return "take_profit"
        if value in {"sl", "stoploss", "stop_loss"}:
            return "stop_loss"
        if value in {"manual", "provider_manual", "external_manual"}:
            return "provider_manual"

    order_type = _normalized_token(item.get("ordType"))
    if order_type in {"takeprofit", "take_profit", "tp"}:
        return "take_profit"
    if order_type in {"stoploss", "stop_loss", "sl"}:
        return "stop_loss"

    reduce_only = _bool_text(item.get("reduceOnly"))
    client_order_id = _text(item.get("clOrdId"))
    if reduce_only is True and not client_order_id.startswith(YTM_OKX_CLIENT_ORDER_PREFIX):
        return "provider_manual"
    return ""


def _normalized_token(value: object) -> str:
    text = _text(value).lower().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _plain_swap_symbol(inst_id: str) -> str:
    normalized = okx_swap_instrument_id(inst_id)
    if normalized.endswith("-SWAP"):
        parts = normalized.removesuffix("-SWAP").split("-")
        if len(parts) == 2 and all(parts):
            return "".join(parts)
    return normalized


def _position_side(*, pos_side: str, quantity: str) -> str:
    normalized = pos_side.lower()
    if normalized in {"long", "short"}:
        return normalized
    if quantity.startswith("-"):
        return "short"
    return "long"


def _compact(payload: dict[str, str | bool]) -> dict[str, str | bool]:
    return {key: value for key, value in payload.items() if value not in {"", None}}


def _bool_text(value: object) -> bool | str:
    text = _text(value)
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    return text


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
