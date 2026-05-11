"""Local mandatory risk policy for leased execution commands."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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
    max_leverage: Decimal
    position_mode: str


@dataclass(frozen=True, slots=True)
class RiskState:
    realized_loss_by_date: dict[str, Decimal]


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
        return RiskState(realized_loss_by_date={})
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
    return RiskState(realized_loss_by_date=losses)


def risk_policy_to_file_payload(policy: RiskPolicy) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "allowedOrderTypes": list(policy.allowed_order_types),
        "allowedMarkets": list(policy.allowed_markets),
        "allowedMarginModes": list(policy.allowed_margin_modes),
        "allowedSymbols": list(policy.allowed_symbols),
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "maxSymbolNotional": {
            symbol: _decimal_text(value)
            for symbol, value in sorted(policy.max_symbol_notional.items())
        },
        "maxLeverage": _decimal_text(policy.max_leverage),
        "paperOnly": policy.paper_only,
        "positionMode": policy.position_mode,
        "version": 1,
    }
    if policy.max_order_notional is not None:
        payload["maxOrderNotional"] = _decimal_text(policy.max_order_notional)
    if policy.max_position_notional is not None:
        payload["maxPositionNotional"] = _decimal_text(policy.max_position_notional)
    if policy.max_daily_loss is not None:
        payload["maxDailyLoss"] = _decimal_text(policy.max_daily_loss)
    return payload


def risk_policy_public_summary(policy: RiskPolicy) -> dict[str, Any]:
    payload = {
        "allowedOrderTypeCount": len(policy.allowed_order_types),
        "allowedMarketCount": len(policy.allowed_markets),
        "allowedMarginModeCount": len(policy.allowed_margin_modes),
        "allowedSymbolCount": len(policy.allowed_symbols),
        "configured": policy.configured,
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "limits": {
            "maxDailyLoss": policy.max_daily_loss is not None,
            "maxOrderNotional": policy.max_order_notional is not None,
            "maxPositionNotional": policy.max_position_notional is not None,
            "maxSymbolNotionalCount": len(policy.max_symbol_notional),
        },
        "paperOnly": policy.paper_only,
        "positionMode": policy.position_mode,
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
        return _block("risk_policy_missing", "local risk policy is not configured")
    if not policy.enabled:
        return _block("risk_policy_disabled", "local risk policy is disabled")
    if policy.kill_switch:
        return _block("risk_kill_switch_enabled", "local risk kill switch is enabled")
    if execution_mode == "real" and policy.paper_only:
        return _block("risk_paper_only", "local risk policy allows paper execution only")

    completeness_errors = policy_completeness_blocks(policy)
    if completeness_errors:
        return _block("risk_policy_incomplete", completeness_errors[0])

    payload = _command_payload(command)
    symbol = _normalized_symbol(
        _first(command, payload, ("symbol", "instrumentId", "instrument", "figi", "ticker"))
    )
    if not symbol:
        return _block("risk_symbol_missing", "command instrument identifier is missing")
    if policy.allowed_symbols and symbol not in policy.allowed_symbols:
        return _block("risk_symbol_not_allowed", "command instrument is not allowed locally")

    order_type = _normalized_order_type(_first(command, payload, ("orderType", "type")))
    if policy.allowed_order_types and not order_type:
        return _block("risk_order_type_missing", "command order type is missing")
    if policy.allowed_order_types and order_type not in policy.allowed_order_types:
        return _block("risk_order_type_not_allowed", "command order type is not allowed locally")

    order_notional = _order_notional(command, payload)
    if policy.max_order_notional is not None and order_notional is None:
        return _block("risk_order_notional_missing", "command order notional is missing")
    if policy.max_order_notional is not None and order_notional > policy.max_order_notional:
        return _block("risk_order_notional_exceeded", "command order notional exceeds local limit")

    market = _normalized_lower_text(
        _first(command, payload, ("market", "brokerMarket", "exchangeMarket")),
        default="",
    )
    if not market:
        return _block("risk_market_missing", "command market is missing")
    if policy.allowed_markets and market not in policy.allowed_markets:
        return _block("risk_market_not_allowed", "command market is not allowed locally")
    futures_block = _evaluate_futures_risk(
        command=command,
        payload=payload,
        market=market,
        policy=policy,
    )
    if futures_block is not None:
        return futures_block

    projected_position = _optional_positive_command_decimal(
        _first(
            command,
            payload,
            (
                "projectedPositionNotional",
                "positionNotionalAfter",
                "postTradePositionNotional",
            ),
        ),
        "projectedPositionNotional",
    )
    symbol_limit = policy.max_symbol_notional.get(symbol)
    if (
        projected_position is None
        and (policy.max_position_notional is not None or symbol_limit is not None)
    ):
        return _block(
            "risk_projected_position_missing",
            "command projected position notional is missing",
        )
    if (
        policy.max_position_notional is not None
        and projected_position > policy.max_position_notional
    ):
        return _block(
            "risk_projected_position_exceeded",
            "command projected position exceeds local limit",
        )
    if symbol_limit is not None and projected_position > symbol_limit:
        return _block(
            "risk_symbol_position_exceeded",
            "command projected symbol position exceeds local limit",
        )

    leverage = _optional_positive_command_decimal(
        _first(command, payload, ("leverage",)),
        "leverage",
    )
    if leverage is None:
        leverage = Decimal("1")
    if leverage > policy.max_leverage:
        return _block("risk_leverage_exceeded", "command leverage exceeds local limit")

    if policy.max_daily_loss is not None:
        today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
        current_loss = state.realized_loss_by_date.get(today, Decimal("0"))
        if current_loss >= policy.max_daily_loss:
            return _block("risk_daily_loss_exceeded", "local daily loss limit is reached")

    return RiskDecision(passed=True)


def policy_configuration_errors(policy: RiskPolicy) -> list[str]:
    errors = []
    if not policy.enabled and not policy.kill_switch:
        errors.append("disabled risk policy must keep killSwitch enabled")
    if policy.max_leverage <= 0:
        errors.append("maxLeverage must be positive")
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
    ):
        if value is not None and value <= 0:
            errors.append(f"{field_name} must be positive")
    for symbol, value in policy.max_symbol_notional.items():
        if value <= 0:
            errors.append(f"maxSymbolNotional.{symbol} must be positive")
    return errors


def policy_completeness_blocks(policy: RiskPolicy) -> list[str]:
    blocks: list[str] = []
    if set(policy.allowed_markets) & FUTURES_MARKETS and policy.position_mode != "one_way":
        blocks.append("local futures risk policy supports one_way positionMode only")
    return blocks


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
    if policy.position_mode != "one_way":
        return _block(
            "risk_futures_position_mode_unsupported",
            "local position mode is unsupported",
        )
    margin_mode = _normalized_lower_text(
        _first(command, payload, ("marginMode", "marginType")),
        default="",
    )
    if policy.allowed_margin_modes and not margin_mode:
        return _block("risk_futures_margin_mode_missing", "command futures margin mode is missing")
    if policy.allowed_margin_modes and margin_mode not in policy.allowed_margin_modes:
        return _block(
            "risk_futures_margin_mode_not_allowed",
            "command futures margin mode is not allowed locally",
        )
    command_position_mode = _normalized_lower_text(
        _first(command, payload, ("positionMode",)),
        default=policy.position_mode,
    )
    if command_position_mode != policy.position_mode:
        return _block(
            "risk_futures_position_mode_mismatch",
            "command futures position mode does not match local policy",
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


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
