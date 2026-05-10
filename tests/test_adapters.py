from __future__ import annotations

from decimal import Decimal

import pytest

from ytm_executor.adapters import build_order_request, deterministic_client_order_id


def test_build_order_request_uses_existing_client_order_id() -> None:
    request = build_order_request(_command(client_order_id="ytm_manual_1"), execution_mode="real")

    assert request.client_order_id == "ytm_manual_1"
    assert request.provider == "binance"
    assert request.symbol == "BTCUSDT"
    assert request.side == "long"
    assert request.position_effect == "open"
    assert request.order_type == "limit"
    assert request.quantity == Decimal("0.01")
    assert request.limit_price == Decimal("100")


def test_deterministic_client_order_id_is_stable_and_bounded() -> None:
    command = _command(command_id="command-abc", client_order_id=None)

    left = deterministic_client_order_id(command)
    right = deterministic_client_order_id(command)

    assert left == right
    assert left.startswith("ytm_")
    assert len(left) == 36


def test_build_order_request_rejects_missing_limit_price() -> None:
    command = _command()
    command["commandPayload"].pop("price")

    with pytest.raises(ValueError, match="limit order price"):
        build_order_request(command, execution_mode="external_paper")


def _command(
    *,
    command_id: str = "command-1",
    client_order_id: str | None = "ytm_existing",
) -> dict[str, object]:
    command: dict[str, object] = {
        "commandPayload": {
            "orderType": "limit",
            "positionEffect": "open",
            "price": "100",
            "quantity": "0.01",
        },
        "id": command_id,
        "provider": "binance",
        "side": "long",
        "symbol": "BTCUSDT",
    }
    if client_order_id is not None:
        command["clientOrderId"] = client_order_id
    return command
