from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ytm_executor.risk import (
    RiskPolicy,
    RiskState,
    evaluate_command_risk,
    read_risk_policy,
    read_risk_state,
    risk_policy_public_summary,
    update_risk_state_from_reconciliation_snapshot,
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
    payload = policy_file.read_text(encoding="utf-8")

    assert loaded.configured is True
    assert loaded.max_daily_loss == Decimal("250")
    assert "allowedSymbols" not in payload
    assert "maxLeverage" not in payload
    assert policy_file.stat().st_mode & 0o777 == 0o600


def test_risk_public_summary_does_not_expose_symbol_list() -> None:
    summary = risk_policy_public_summary(_policy())

    assert summary["configured"] is True
    assert summary["appManagedTradingLimits"] is True
    assert "BTCUSDT" not in repr(summary)


def test_risk_policy_allows_skipped_local_limits(tmp_path: Path) -> None:
    policy_file = tmp_path / "risk-policy.json"

    write_risk_policy(
        policy_file,
        RiskPolicy(
            configured=True,
            enabled=True,
            kill_switch=False,
            paper_only=True,
            allowed_markets=(),
            allowed_margin_modes=(),
            allowed_symbols=(),
            allowed_order_types=(),
            max_order_notional=None,
            max_position_notional=None,
            max_symbol_notional={},
            max_daily_loss=None,
            max_total_drawdown=None,
            max_leverage=Decimal("1"),
            position_mode="one_way",
        ),
    )

    decision = evaluate_command_risk(
        _command(),
        execution_mode="external_paper",
        policy=read_risk_policy(policy_file),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


def test_risk_ignores_app_owned_symbol_limits_locally() -> None:
    command = _command()
    command["symbol"] = "ETHUSDT"

    decision = evaluate_command_risk(
        command,
        execution_mode="external_paper",
        policy=_policy(),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


def test_risk_allows_command_inside_local_limits() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="external_paper",
        policy=_policy(),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


def test_risk_rejects_real_when_policy_is_paper_only() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="real",
        policy=_policy(paper_only=True),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_paper_only"


def test_risk_rejects_daily_loss_limit_reached() -> None:
    decision = evaluate_command_risk(
        _command(),
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=_state(losses={"2026-05-10": Decimal("250")}),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_daily_loss_exceeded"


def test_risk_state_updates_drawdown_from_reconciliation_snapshot(tmp_path: Path) -> None:
    state_file = tmp_path / "risk-state.json"

    update_risk_state_from_reconciliation_snapshot(
        path=state_file,
        snapshot={
            "balances": [
                {"currency": "USDT", "equity": "980", "usdEquity": "980"},
            ]
        },
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    state = read_risk_state(state_file)
    assert state.initial_equity == Decimal("980")
    assert state.current_equity == Decimal("980")
    assert state.realized_loss_by_date["2026-05-10"] == Decimal("0")

    update_risk_state_from_reconciliation_snapshot(
        path=state_file,
        snapshot={
            "balances": [
                {"currency": "USDT", "equity": "900", "usdEquity": "900"},
            ]
        },
        now=datetime(2026, 5, 10, 11, 1, tzinfo=UTC),
    )

    state = read_risk_state(state_file)
    assert state.initial_equity == Decimal("980")
    assert state.current_equity == Decimal("900")
    assert state.realized_loss_by_date["2026-05-10"] == Decimal("80")
    assert state_file.stat().st_mode & 0o777 == 0o600


def test_risk_rejects_futures_margin_mode_not_allowed() -> None:
    command = _command()
    command["commandPayload"]["marginMode"] = "portfolio"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_margin_mode_unsupported"


def test_risk_applies_futures_margin_gate_to_okx_swap() -> None:
    command = _command(market="okx_swap")
    command["commandPayload"]["marginMode"] = "portfolio"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False, allowed_markets=("okx_swap",)),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_margin_mode_unsupported"


def test_risk_ignores_app_owned_position_limits_locally() -> None:
    command = _command()
    command["commandPayload"]["projectedPositionNotional"] = "5001"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=replace(_policy(paper_only=False), max_position_notional=Decimal("10000")),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


def test_risk_rejects_futures_reduce_only_disabled_for_close() -> None:
    command = _command()
    command["commandPayload"]["positionEffect"] = "close"
    command["commandPayload"]["reduceOnly"] = False

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is False
    assert decision.reason_code == "risk_futures_reduce_only_required"


def test_risk_ignores_app_owned_leverage_limits_locally() -> None:
    command = _command()
    command["commandPayload"]["leverage"] = "1.1"

    decision = evaluate_command_risk(
        command,
        execution_mode="real",
        policy=_policy(paper_only=False),
        state=_state(),
        now=datetime(2026, 5, 10, 10, 1, tzinfo=UTC),
    )

    assert decision.passed is True


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
        max_total_drawdown=None,
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
            "riskControls": {"riskDecisionId": "risk-1", "source": "ytm"},
        },
        "symbol": "BTCUSDT",
    }


def _state(
    *,
    losses: dict[str, Decimal] | None = None,
    initial_equity: Decimal = Decimal("1000"),
    current_equity: Decimal = Decimal("1000"),
) -> RiskState:
    return RiskState(
        realized_loss_by_date=losses if losses is not None else {"2026-05-10": Decimal("0")},
        daily_equity_open_by_date={"2026-05-10": Decimal("1000")},
        initial_equity=initial_equity,
        current_equity=current_equity,
    )
