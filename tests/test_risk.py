from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ytm_executor.risk import (
    RiskPolicy,
    RiskState,
    evaluate_command_risk,
    read_risk_policy,
    risk_policy_public_summary,
    write_risk_policy,
)


def test_missing_risk_policy_is_fail_closed(tmp_path: Path) -> None:
    policy = read_risk_policy(tmp_path / "risk-policy.json")

    assert policy.configured is False
    assert policy.kill_switch is True


def test_write_and_read_local_risk_policy(tmp_path: Path) -> None:
    policy_file = tmp_path / "risk-policy.json"
    write_risk_policy(policy_file, _policy())

    loaded = read_risk_policy(policy_file)

    assert loaded.configured is True
    assert loaded.allowed_symbols == ("BTCUSDT", "SBER")
    assert loaded.allowed_markets == ("usdm_futures",)
    assert loaded.allowed_margin_modes == ("cross",)
    assert loaded.allowed_order_types == ("limit", "market")
    assert loaded.max_order_notional == Decimal("1000")
    assert loaded.max_symbol_notional == {"BTCUSDT": Decimal("5000"), "SBER": Decimal("5000")}
    assert policy_file.stat().st_mode & 0o777 == 0o600


def test_risk_public_summary_does_not_expose_symbol_list() -> None:
    summary = risk_policy_public_summary(_policy())

    assert summary["configured"] is True
    assert summary["allowedSymbolCount"] == 2
    assert "BTCUSDT" not in repr(summary)


def test_risk_policy_rejects_incomplete_execution_policy(tmp_path: Path) -> None:
    policy_file = tmp_path / "risk-policy.json"

    with pytest.raises(ValueError, match="allowedSymbols"):
        write_risk_policy(
            policy_file,
            RiskPolicy(
                configured=True,
                enabled=True,
                kill_switch=False,
                paper_only=True,
                allowed_markets=("usdm_futures",),
                allowed_margin_modes=("cross",),
                allowed_symbols=(),
                allowed_order_types=("limit",),
                max_order_notional=Decimal("1000"),
                max_position_notional=Decimal("5000"),
                max_symbol_notional={},
                max_daily_loss=Decimal("250"),
                max_leverage=Decimal("1"),
                position_mode="one_way",
            ),
        )


def test_risk_allows_command_inside_local_limits() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="external_paper",
        policy=_policy(),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


def test_risk_rejects_real_when_policy_is_paper_only() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="real",
        policy=_policy(paper_only=True),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_paper_only"


def test_risk_rejects_daily_loss_limit_reached() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="external_paper",
        policy=_policy(),
        state=RiskState(realized_loss_by_date={"2026-05-10": Decimal("250")}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_daily_loss_exceeded"


def test_risk_rejects_futures_margin_mode_not_allowed() -> None:
    command = _command()
    command["commandPayload"]["marginMode"] = "isolated"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_margin_mode_not_allowed"


def test_risk_applies_futures_margin_gate_to_okx_swap() -> None:
    command = _command(market="okx_swap")
    command["commandPayload"]["marginMode"] = "isolated"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False, allowed_markets=("okx_swap",)),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_margin_mode_not_allowed"


def test_risk_rejects_symbol_position_limit() -> None:
    command = _command()
    command["commandPayload"]["projectedPositionNotional"] = "5001"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=replace(_policy(paper_only=False), max_position_notional=Decimal("10000")),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_symbol_position_exceeded"


def test_risk_rejects_futures_reduce_only_disabled_for_close() -> None:
    command = _command()
    command["commandPayload"]["positionEffect"] = "close"
    command["commandPayload"]["reduceOnly"] = False

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=RiskState(realized_loss_by_date={}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_reduce_only_required"


def _policy(
    *,
    paper_only: bool = True,
    allowed_markets: tuple[str, ...] = ("usdm_futures",),
) -> RiskPolicy:
    return RiskPolicy(
        configured=True,
        enabled=True,
        kill_switch=False,
        paper_only=paper_only,
        allowed_markets=allowed_markets,
        allowed_margin_modes=("cross",),
        allowed_symbols=("BTCUSDT", "SBER"),
        allowed_order_types=("limit", "market"),
        max_order_notional=Decimal("1000"),
        max_position_notional=Decimal("5000"),
        max_symbol_notional={"BTCUSDT": Decimal("5000"), "SBER": Decimal("5000")},
        max_daily_loss=Decimal("250"),
        max_leverage=Decimal("1"),
        position_mode="one_way",
    )


def _command(*, market: str = "usdm_futures") -> dict[str, object]:
    return {
        "commandPayload": {
            "leverage": "1",
            "marginMode": "cross",
            "market": market,
            "orderNotional": "100",
            "orderType": "limit",
            "positionEffect": "open",
            "projectedPositionNotional": "100",
        },
        "symbol": "BTCUSDT",
    }
