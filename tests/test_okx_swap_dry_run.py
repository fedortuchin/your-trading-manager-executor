from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import ytm_executor.okx_swap as okx_swap
from ytm_executor.okx_swap import OKX_ORDER_PRECHECK_PATH, OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER
from ytm_executor.preflight import preflight_command
from ytm_executor.risk import RiskPolicy, RiskState
from ytm_executor.secret_store import CredentialSummary


def test_real_okx_swap_dry_run_reaches_order_precheck_without_placement(monkeypatch) -> None:
    api = FakeOkxSwapApi()
    monkeypatch.setattr(
        okx_swap,
        "_build_mainnet_api",
        lambda *, api_key, api_secret, passphrase: api,
    )

    decision = preflight_command(
        _leased_real_okx_command(),
        local_credentials=(CredentialSummary(provider="okx", name="main"),),
        local_secret_resolver=lambda provider, name: {
            "apiKey": f"{provider}-{name}-public",
            "apiSecret": "okx-private-secret",
            "passphrase": "okx-passphrase",
        },
        risk_policy=_risk_policy(),
        risk_state=RiskState(realized_loss_by_date={}),
        validation_summaries=(
            {
                "checkedAt": "2026-05-10T10:00:00Z",
                "name": "main",
                "permissions": {
                    "accountReadable": True,
                    "tradingAllowed": False,
                    "withdrawalsAllowed": False,
                },
                "provider": "okx",
                "status": "passed",
                "warnings": ["trade_permission_not_verified_by_read_only_validation"],
            },
        ),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert api.instrument_calls == 1
    assert len(api.precheck_calls) == 1
    assert api.precheck_calls[0]["endpoint"] == OKX_ORDER_PRECHECK_PATH
    assert decision.status == "rejected"
    assert decision.result_payload["preflightReasonCode"] == "real_execution_disabled"
    assert decision.result_payload["adapterPreflight"] == "passed"
    assert decision.result_payload["adapterResult"]["executorAction"] == "order_precheck_validated"
    assert decision.result_payload["adapterResult"]["normalizedOrder"]["sz"] == "12"
    assert "okx-private-secret" not in repr(decision.result_payload)
    assert "okx-passphrase" not in repr(decision.result_payload)


class FakeOkxSwapApi:
    def __init__(self) -> None:
        self.instrument_calls = 0
        self.precheck_calls: list[dict[str, object]] = []

    def get_instruments(self, *, inst_type: str, inst_id: str):
        self.instrument_calls += 1
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {
            "code": "0",
            "data": [
                {
                    "ctVal": "0.1",
                    "instId": "BTC-USDT-SWAP",
                    "instType": "SWAP",
                    "lotSz": "0.1",
                    "minSz": "0.1",
                    "state": "live",
                    "tickSz": "0.1",
                }
            ],
            "msg": "",
        }

    def order_precheck(self, params: dict[str, str]):
        self.precheck_calls.append({"endpoint": OKX_ORDER_PRECHECK_PATH, "params": params})
        return {"code": "0", "data": [], "msg": ""}

    def place_order(self, **kwargs):
        raise AssertionError("place_order must not be called")


def _leased_real_okx_command() -> dict[str, object]:
    return {
        "command": {
            "clientOrderId": "ytm_command_1",
            "commandPayload": {
                "adapter": OKX_SWAP_MAINNET_ORDER_PRECHECK_ADAPTER,
                "leverage": "1",
                "marginMode": "cross",
                "market": "okx_swap",
                "orderNotional": "120",
                "orderType": "limit",
                "positionEffect": "open",
                "price": "100",
                "priceReference": "100",
                "projectedPositionNotional": "120",
            },
            "executionAccountSource": "provider",
            "executionMode": "real",
            "id": "command-1",
            "provider": "okx",
            "side": "long",
            "status": "created",
            "symbol": "BTCUSDT",
        },
        "lease": {"id": "lease-1"},
    }


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        configured=True,
        enabled=True,
        kill_switch=False,
        paper_only=False,
        allowed_markets=("okx_swap",),
        allowed_margin_modes=("cross",),
        allowed_symbols=("BTCUSDT",),
        allowed_order_types=("limit",),
        max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("5000"),
        max_symbol_notional={"BTCUSDT": Decimal("5000")},
        max_daily_loss=Decimal("250"),
        max_leverage=Decimal("1"),
        position_mode="one_way",
    )
