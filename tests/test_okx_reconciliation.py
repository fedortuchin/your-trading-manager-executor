from __future__ import annotations

from datetime import UTC, datetime

from ytm_executor.reconciliation import OkxSwapReconciliationAdapter


def test_okx_reconciliation_snapshot_is_sanitized_and_normalized() -> None:
    api = FakeOkxReconciliationApi()
    adapter = OkxSwapReconciliationAdapter(
        api_key="okx-public-key",
        api_secret="okx-private-secret",
        passphrase="okx-passphrase",
        api=api,
    )

    snapshot = adapter.capture_snapshot(now=datetime(2026, 5, 10, 10, 15, tzinfo=UTC))

    assert api.balance_calls == 1
    assert api.positions_calls == [{"inst_id": "", "inst_type": "SWAP", "pos_id": ""}]
    assert api.order_calls == [{"inst_type": "SWAP", "state": ""}]
    assert snapshot["provider"] == "okx"
    assert snapshot["market"] == "okx_swap"
    assert snapshot["capturedAt"] == "2026-05-10T10:15:00Z"
    assert snapshot["balances"] == [
        {
            "availableBalance": "100",
            "cashBalance": "120",
            "currency": "USDT",
            "equity": "120",
            "frozenBalance": "20",
            "usdEquity": "120",
        }
    ]
    assert snapshot["positions"] == [
        {
            "averageEntryPrice": "100",
            "instrumentId": "BTC-USDT-SWAP",
            "leverage": "1",
            "marginMode": "cross",
            "markPrice": "101",
            "notionalUsd": "202",
            "positionId": "pos-1",
            "positionSide": "net",
            "quantity": "2",
            "side": "long",
            "symbol": "BTCUSDT",
            "unrealizedPnl": "2",
        }
    ]
    assert snapshot["openOrders"] == [
        {
            "averageFillPrice": "0",
            "clientOrderId": "ytm-order-1",
            "filledQuantity": "0",
            "instrumentId": "BTC-USDT-SWAP",
            "orderType": "limit",
            "positionSide": "net",
            "price": "100",
            "providerOrderId": "order-1",
            "quantity": "2",
            "reduceOnly": False,
            "side": "buy",
            "state": "live",
            "symbol": "BTCUSDT",
        }
    ]
    assert "okx-private-secret" not in repr(snapshot)
    assert "okx-passphrase" not in repr(snapshot)


class FakeOkxReconciliationApi:
    def __init__(self) -> None:
        self.balance_calls = 0
        self.positions_calls: list[dict[str, str]] = []
        self.order_calls: list[dict[str, str]] = []

    def get_account_balance(self, ccy: str = ""):
        self.balance_calls += 1
        assert ccy == ""
        return {
            "code": "0",
            "data": [
                {
                    "details": [
                        {
                            "availBal": "100",
                            "cashBal": "120",
                            "ccy": "USDT",
                            "eq": "120",
                            "eqUsd": "120",
                            "frozenBal": "20",
                        }
                    ]
                }
            ],
            "msg": "",
        }

    def get_positions(self, inst_type: str = "", inst_id: str = "", pos_id: str = ""):
        self.positions_calls.append(
            {"inst_id": inst_id, "inst_type": inst_type, "pos_id": pos_id}
        )
        return {
            "code": "0",
            "data": [
                {
                    "avgPx": "100",
                    "instId": "BTC-USDT-SWAP",
                    "lever": "1",
                    "markPx": "101",
                    "mgnMode": "cross",
                    "notionalUsd": "202",
                    "pos": "2",
                    "posId": "pos-1",
                    "posSide": "net",
                    "upl": "2",
                }
            ],
            "msg": "",
        }

    def get_order_list(self, inst_type: str = "", state: str = ""):
        self.order_calls.append({"inst_type": inst_type, "state": state})
        return {
            "code": "0",
            "data": [
                {
                    "accFillSz": "0",
                    "avgPx": "0",
                    "clOrdId": "ytm-order-1",
                    "instId": "BTC-USDT-SWAP",
                    "ordId": "order-1",
                    "ordType": "limit",
                    "posSide": "net",
                    "px": "100",
                    "reduceOnly": "false",
                    "side": "buy",
                    "state": "live",
                    "sz": "2",
                }
            ],
            "msg": "",
        }
