"""Provider reconciliation snapshot capture from the executor host."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ytm_executor.guards import reject_secret_fields
from ytm_executor.okx_swap import _okx_response_data, okx_swap_instrument_id


class OkxReconciliationApi(Protocol):
    def get_account_balance(self, ccy: str = "") -> dict[str, Any]: ...
    def get_positions(
        self,
        inst_type: str = "",
        inst_id: str = "",
        pos_id: str = "",
    ) -> dict[str, Any]: ...
    def get_order_list(self, inst_type: str = "", state: str = "") -> dict[str, Any]: ...


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
        snapshot = {
            "balances": _sanitize_balances(balances),
            "capturedAt": _iso_z(captured_at),
            "market": "okx_swap",
            "openOrders": _sanitize_orders(orders),
            "positions": _sanitize_positions(positions),
            "provider": "okx",
            "source": "executor_okx_reconciliation",
            "zeroSecret": True,
        }
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
        orders.append(
            _compact(
                {
                    "averageFillPrice": _text(item.get("avgPx")),
                    "clientOrderId": _text(item.get("clOrdId")),
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
