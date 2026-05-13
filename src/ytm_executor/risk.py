"""Local executor fail-safe for leased execution commands.

YTM owns trading limits such as symbols, notional, leverage, and max open trades.
The executor only keeps controls that must stay local even if YTM is unhealthy:
kill switch, paper-only gate, and account-level drawdown stops.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ytm_executor.guards import reject_secret_fields
from ytm_executor.state import DEFAULT_HOME

DEFAULT_RISK_POLICY_FILE = DEFAULT_HOME / "risk-policy.json"
DEFAULT_RISK_STATE_FILE = DEFAULT_HOME / "risk-state.json"
SUPPORTED_ORDER_TYPES = frozenset({"limit", "market", "stop", "stop_limit", "stop_market"})
SUPPORTED_MARKETS = frozenset({"okx_swap", "usdm_futures"})
FUTURES_MARKETS = frozenset({"okx_swap", "usdm_futures"})
SUPPORTED_MARGIN_MODES = frozenset({"cross", "isolated"})
SUPPORTED_POSITION_MODES = frozenset({"one_way"})


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    configured: bool
    enabled: bool
    kill_switch: bool
    paper_only: bool
    allowed_markets: tuple[str, ...]
    allowed_margin_modes: tuple[str, ...]
    allowed_symbols: tuple[str, ...]
    allowed_order_types: tuple[str, ...]
    max_order_notional: Decimal | None
    max_position_notional: Decimal | None
    max_symbol_notional: dict[str, Decimal]
    max_daily_loss: Decimal | None
    max_total_drawdown: Decimal | None
    max_leverage: Decimal
    position_mode: str


@dataclass(frozen=True, slots=True)
class RiskState:
    realized_loss_by_date: dict[str, Decimal]
    daily_equity_open_by_date: dict[str, Decimal]
    initial_equity: Decimal | None = None
    current_equity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    passed: bool
    reason_code: str | None = None
    reason: str | None = None


def missing_risk_policy() -> RiskPolicy:
    return RiskPolicy(
        configured=False,
        enabled=False,
        kill_switch=True,
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
    )


def read_risk_policy(path: Path) -> RiskPolicy:
    if not path.exists():
        return missing_risk_policy()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("risk policy must be a JSON object")
    reject_secret_fields(payload)
    version = payload.get("version", 1)
    if version != 1:
        raise ValueError("risk policy version is unsupported")
    policy = RiskPolicy(
        configured=True,
        enabled=_bool(payload, "enabled", default=True),
        kill_switch=_bool(payload, "killSwitch", default=True),
        paper_only=_bool(payload, "paperOnly", default=True),
        allowed_markets=_normalized_lower_text_tuple(payload.get("allowedMarkets")),
        allowed_margin_modes=_normalized_lower_text_tuple(payload.get("allowedMarginModes")),
        allowed_symbols=_normalized_text_tuple(payload.get("allowedSymbols"), upper=True),
        allowed_order_types=_normalized_order_types(payload.get("allowedOrderTypes")),
        max_order_notional=_optional_decimal(payload.get("maxOrderNotional"), "maxOrderNotional"),
        max_position_notional=_optional_decimal(
            payload.get("maxPositionNotional"),
            "maxPositionNotional",
        ),
        max_symbol_notional=_normalized_symbol_decimal_map(
            payload.get("maxSymbolNotional"),
            "maxSymbolNotional",
        ),
        max_daily_loss=_optional_decimal(payload.get("maxDailyLoss"), "maxDailyLoss"),
        max_total_drawdown=_optional_decimal(
            payload.get("maxTotalDrawdown"),
            "maxTotalDrawdown",
        ),
        max_leverage=_positive_decimal(payload.get("maxLeverage", "1"), "maxLeverage"),
        position_mode=_normalized_lower_text(payload.get("positionMode"), default="one_way"),
    )
    errors = policy_configuration_errors(policy)
    if errors:
        raise ValueError("; ".join(errors))
    return policy


def write_risk_policy(path: Path, policy: RiskPolicy) -> None:
    errors = policy_configuration_errors(policy)
    if errors:
        raise ValueError("; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(risk_policy_to_file_payload(policy), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def read_risk_state(path: Path) -> RiskState:
    if not path.exists():
        return RiskState(realized_loss_by_date={}, daily_equity_open_by_date={})
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("risk state must be a JSON object")
    reject_secret_fields(payload)
    raw_losses = payload.get("realizedLossByDate", {})
    if not isinstance(raw_losses, dict):
        raise ValueError("risk state realizedLossByDate must be an object")
    losses: dict[str, Decimal] = {}
    for date, value in raw_losses.items():
        if not isinstance(date, str):
            raise ValueError("risk state dates must be strings")
        losses[date] = _non_negative_decimal(value, f"realizedLossByDate.{date}")
    raw_daily_equity = payload.get("dailyEquityOpenByDate", {})
    if not isinstance(raw_daily_equity, dict):
        raise ValueError("risk state dailyEquityOpenByDate must be an object")
    daily_equity: dict[str, Decimal] = {}
    for date, value in raw_daily_equity.items():
        if not isinstance(date, str):
            raise ValueError("risk state dates must be strings")
        daily_equity[date] = _non_negative_decimal(value, f"dailyEquityOpenByDate.{date}")
    return RiskState(
        realized_loss_by_date=losses,
        daily_equity_open_by_date=daily_equity,
        initial_equity=_optional_decimal(payload.get("initialEquity"), "initialEquity"),
        current_equity=_optional_decimal(payload.get("currentEquity"), "currentEquity"),
    )


def write_risk_state(path: Path, state: RiskState) -> None:
    payload = {
        "currentEquity": _decimal_text(state.current_equity),
        "dailyEquityOpenByDate": {
            date: _decimal_text(value)
            for date, value in sorted(state.daily_equity_open_by_date.items())
        },
        "initialEquity": _decimal_text(state.initial_equity),
        "realizedLossByDate": {
            date: _decimal_text(value)
            for date, value in sorted(state.realized_loss_by_date.items())
        },
        "version": 1,
    }
    reject_secret_fields(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def update_risk_state_from_reconciliation_snapshot(
    *,
    path: Path,
    snapshot: dict[str, Any],
    now: datetime | None = None,
) -> RiskState:
    current = read_risk_state(path)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    today = timestamp.date().isoformat()
    current_equity = _snapshot_usd_equity(snapshot)
    initial_equity = current.initial_equity or current_equity
    daily_open = dict(current.daily_equity_open_by_date)
    if current_equity is not None:
        daily_open.setdefault(today, current_equity)
    losses = dict(current.realized_loss_by_date)
    if current_equity is not None and today in daily_open:
        loss = daily_open[today] - current_equity
        losses[today] = loss if loss > 0 else Decimal("0")
    next_state = RiskState(
        realized_loss_by_date=losses,
        daily_equity_open_by_date=daily_open,
        initial_equity=initial_equity,
        current_equity=current_equity or current.current_equity,
    )
    write_risk_state(path, next_state)
    return next_state


def risk_policy_to_file_payload(policy: RiskPolicy) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "paperOnly": policy.paper_only,
        "version": 1,
    }
    if policy.max_daily_loss is not None:
        payload["maxDailyLoss"] = _decimal_text(policy.max_daily_loss)
    if policy.max_total_drawdown is not None:
        payload["maxTotalDrawdown"] = _decimal_text(policy.max_total_drawdown)
    return payload


def risk_policy_public_summary(policy: RiskPolicy) -> dict[str, Any]:
    payload = {
        "appManagedTradingLimits": True,
        "configured": policy.configured,
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "limits": {
            "maxDailyLoss": policy.max_daily_loss is not None,
            "maxTotalDrawdown": policy.max_total_drawdown is not None,
        },
        "paperOnly": policy.paper_only,
    }
    reject_secret_fields(payload)
    return payload


def evaluate_command_risk(
    command: dict[str, Any],
    *,
    execution_mode: str,
    policy: RiskPolicy,
    state: RiskState,
    now: datetime | None = None,
) -> RiskDecision:
    if not policy.configured:
        return _block("risk_policy_missing", "local fail-safe policy is not configured")
    if not policy.enabled:
        return _block("risk_policy_disabled", "local fail-safe policy is disabled")
    if policy.kill_switch:
        return _block("risk_kill_switch_enabled", "local fail-safe kill switch is enabled")
    if execution_mode == "real" and policy.paper_only:
        return _block("risk_paper_only", "local fail-safe allows paper execution only")

    completeness_errors = policy_completeness_blocks(policy)
    if completeness_errors:
        return _block("risk_policy_incomplete", completeness_errors[0])

    payload = _command_payload(command)
    risk_attestation = payload.get("riskControls")
    if (
        execution_mode == "real"
        and (not isinstance(risk_attestation, dict) or risk_attestation.get("source") != "ytm")
    ):
        return _block(
            "risk_controls_missing",
            "real commands require YTM risk-controls attestation",
        )

    market = _normalized_lower_text(
        _first(command, payload, ("market", "brokerMarket", "exchangeMarket")),
        default="",
    )
    if not market:
        return _block("risk_market_missing", "command market is missing")
    futures_block = _evaluate_futures_risk(
        command=command,
        payload=payload,
        market=market,
        policy=policy,
    )
    if futures_block is not None:
        return futures_block

    if execution_mode == "real" and policy.max_daily_loss is not None:
        today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        current_loss = state.realized_loss_by_date.get(today)
        if current_loss is None:
            return _block("risk_state_missing", "local daily drawdown state is missing")
        if current_loss >= policy.max_daily_loss:
            return _block("risk_daily_loss_exceeded", "local daily loss limit is reached")
    if execution_mode == "real" and policy.max_total_drawdown is not None:
        if state.initial_equity is None or state.current_equity is None:
            return _block("risk_state_missing", "local total drawdown state is missing")
        current_drawdown = state.initial_equity - state.current_equity
        if current_drawdown >= policy.max_total_drawdown:
            return _block("risk_total_drawdown_exceeded", "local total drawdown limit is reached")

    return RiskDecision(passed=True)


def policy_configuration_errors(policy: RiskPolicy) -> list[str]:
    errors = []
    if not policy.enabled and not policy.kill_switch:
        errors.append("disabled risk policy must keep killSwitch enabled")
    invalid_markets = sorted(set(policy.allowed_markets) - SUPPORTED_MARKETS)
    if invalid_markets:
        errors.append(f"unsupported markets: {', '.join(invalid_markets)}")
    invalid_margin_modes = sorted(set(policy.allowed_margin_modes) - SUPPORTED_MARGIN_MODES)
    if invalid_margin_modes:
        errors.append(f"unsupported margin modes: {', '.join(invalid_margin_modes)}")
    if policy.position_mode not in SUPPORTED_POSITION_MODES:
        errors.append(f"unsupported position mode: {policy.position_mode}")
    invalid_types = sorted(set(policy.allowed_order_types) - SUPPORTED_ORDER_TYPES)
    if invalid_types:
        errors.append(f"unsupported order types: {', '.join(invalid_types)}")
    for field_name, value in (
        ("maxOrderNotional", policy.max_order_notional),
        ("maxPositionNotional", policy.max_position_notional),
        ("maxDailyLoss", policy.max_daily_loss),
        ("maxTotalDrawdown", policy.max_total_drawdown),
    ):
        if value is not None and value <= 0:
            errors.append(f"{field_name} must be positive")
    for symbol, value in policy.max_symbol_notional.items():
        if value <= 0:
            errors.append(f"maxSymbolNotional.{symbol} must be positive")
    return errors


def policy_completeness_blocks(policy: RiskPolicy) -> list[str]:
    return []


def _block(reason_code: str, reason: str) -> RiskDecision:
    return RiskDecision(passed=False, reason_code=reason_code, reason=reason)


def _evaluate_futures_risk(
    *,
    command: dict[str, Any],
    payload: dict[str, Any],
    market: str,
    policy: RiskPolicy,
) -> RiskDecision | None:
    if market not in FUTURES_MARKETS:
        return None
    margin_mode = _normalized_lower_text(
        _first(command, payload, ("marginMode", "marginType")),
        default="",
    )
    if not margin_mode:
        return _block("risk_futures_margin_mode_missing", "command futures margin mode is missing")
    if margin_mode not in SUPPORTED_MARGIN_MODES:
        return _block(
            "risk_futures_margin_mode_unsupported",
            "command futures margin mode is unsupported",
        )
    command_position_mode = _normalized_lower_text(
        _first(command, payload, ("positionMode",)),
        default="one_way",
    )
    if command_position_mode != "one_way":
        return _block(
            "risk_futures_position_mode_unsupported",
            "command futures position mode is unsupported",
        )
    if _bool_like(_first(command, payload, ("closePosition",))) is True:
        return _block("risk_futures_close_position_unsupported", "closePosition is not enabled")
    reduce_only = _bool_like(_first(command, payload, ("reduceOnly",)))
    position_effect = _normalized_lower_text(
        _first(command, payload, ("positionEffect", "effect")),
        default="",
    )
    if position_effect == "open" and reduce_only is True:
        return _block("risk_futures_reduce_only_open", "open commands cannot be reduceOnly")
    if position_effect in {"reduce", "close"} and reduce_only is False:
        return _block(
            "risk_futures_reduce_only_required",
            "reduce/close commands cannot disable reduceOnly",
        )
    return None


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


def _order_notional(command: dict[str, Any], payload: dict[str, Any]) -> Decimal | None:
    direct = _optional_positive_command_decimal(
        _first(command, payload, ("orderNotional", "notional", "quoteQuantity")),
        "orderNotional",
    )
    if direct is not None:
        return direct
    quantity = _optional_positive_command_decimal(
        _first(command, payload, ("quantity", "qty", "baseQuantity", "lots")),
        "quantity",
    )
    price = _optional_positive_command_decimal(
        _first(command, payload, ("price", "limitPrice", "estimatedPrice", "entryPrice")),
        "price",
    )
    if quantity is None or price is None:
        return None
    return quantity * price


def _normalized_symbol(value: object) -> str:
    return str(value).strip().upper() if isinstance(value, str) else ""


def _normalized_order_type(value: object) -> str:
    return str(value).strip().lower().replace("-", "_") if isinstance(value, str) else ""


def _normalized_text_tuple(value: object, *, upper: bool) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("risk policy list fields must be string arrays")
    result: list[str] = []
    seen = set()
    for item in value:
        normalized = item.strip()
        if upper:
            normalized = normalized.upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _normalized_lower_text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("risk policy list fields must be string arrays")
    result: list[str] = []
    seen = set()
    for item in value:
        normalized = _normalized_lower_text(item, default="")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _normalized_order_types(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("risk policy list fields must be string arrays")
    result: list[str] = []
    seen = set()
    for item in value:
        normalized = _normalized_order_type(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _normalized_symbol_decimal_map(value: object, field_name: str) -> dict[str, Decimal]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    result: dict[str, Decimal] = {}
    for raw_symbol, raw_limit in value.items():
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError(f"{field_name} symbols must be strings")
        symbol = raw_symbol.strip().upper()
        result[symbol] = _positive_decimal(raw_limit, f"{field_name}.{symbol}")
    return result


def _normalized_lower_text(value: object, *, default: str) -> str:
    if value is None:
        return default
    return str(value).strip().lower().replace("-", "_") if isinstance(value, str) else default


def _bool_like(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _positive_decimal(value, field_name)


def _optional_positive_command_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _positive_decimal(value, field_name)


def _positive_decimal(value: object, field_name: str) -> Decimal:
    decimal = _decimal(value, field_name)
    if decimal <= 0:
        raise ValueError(f"{field_name} must be positive")
    return decimal


def _non_negative_decimal(value: object, field_name: str) -> Decimal:
    decimal = _decimal(value, field_name)
    if decimal < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return decimal


def _command_leverage(value: Decimal) -> Decimal:
    return max(Decimal("1"), value.to_integral_value(rounding=ROUND_CEILING))


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not decimal.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return decimal


def _snapshot_usd_equity(snapshot: dict[str, Any]) -> Decimal | None:
    balances = snapshot.get("balances")
    if not isinstance(balances, list):
        return None
    candidates: list[Decimal] = []
    for item in balances:
        if not isinstance(item, dict):
            continue
        currency = str(item.get("currency") or "").upper()
        if currency not in {"USDT", "USD"}:
            continue
        value = item.get("usdEquity") or item.get("equity")
        if value in (None, ""):
            continue
        candidates.append(_non_negative_decimal(value, "balance.usdEquity"))
    if not candidates:
        return None
    return max(candidates)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")
