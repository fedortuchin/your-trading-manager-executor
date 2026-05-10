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


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    configured: bool
    enabled: bool
    kill_switch: bool
    paper_only: bool
    allowed_symbols: tuple[str, ...]
    allowed_order_types: tuple[str, ...]
    max_order_notional: Decimal | None
    max_position_notional: Decimal | None
    max_daily_loss: Decimal | None
    max_leverage: Decimal


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
        allowed_symbols=(),
        allowed_order_types=(),
        max_order_notional=None,
        max_position_notional=None,
        max_daily_loss=None,
        max_leverage=Decimal("1"),
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
        allowed_symbols=_normalized_text_tuple(payload.get("allowedSymbols"), upper=True),
        allowed_order_types=_normalized_order_types(payload.get("allowedOrderTypes")),
        max_order_notional=_optional_decimal(payload.get("maxOrderNotional"), "maxOrderNotional"),
        max_position_notional=_optional_decimal(
            payload.get("maxPositionNotional"),
            "maxPositionNotional",
        ),
        max_daily_loss=_optional_decimal(payload.get("maxDailyLoss"), "maxDailyLoss"),
        max_leverage=_positive_decimal(payload.get("maxLeverage", "1"), "maxLeverage"),
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
        "allowedSymbols": list(policy.allowed_symbols),
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "maxLeverage": _decimal_text(policy.max_leverage),
        "paperOnly": policy.paper_only,
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
        "allowedSymbolCount": len(policy.allowed_symbols),
        "configured": policy.configured,
        "enabled": policy.enabled,
        "killSwitch": policy.kill_switch,
        "limits": {
            "maxDailyLoss": policy.max_daily_loss is not None,
            "maxOrderNotional": policy.max_order_notional is not None,
            "maxPositionNotional": policy.max_position_notional is not None,
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
    if symbol not in policy.allowed_symbols:
        return _block("risk_symbol_not_allowed", "command instrument is not allowed locally")

    order_type = _normalized_order_type(_first(command, payload, ("orderType", "type")))
    if not order_type:
        return _block("risk_order_type_missing", "command order type is missing")
    if order_type not in policy.allowed_order_types:
        return _block("risk_order_type_not_allowed", "command order type is not allowed locally")

    order_notional = _order_notional(command, payload)
    if order_notional is None:
        return _block("risk_order_notional_missing", "command order notional is missing")
    if policy.max_order_notional is not None and order_notional > policy.max_order_notional:
        return _block("risk_order_notional_exceeded", "command order notional exceeds local limit")

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
    if projected_position is None:
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
    if not policy.kill_switch:
        errors.extend(policy_completeness_blocks(policy))
    if policy.max_leverage <= 0:
        errors.append("maxLeverage must be positive")
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
    return errors


def policy_completeness_blocks(policy: RiskPolicy) -> list[str]:
    blocks = []
    if not policy.allowed_symbols:
        blocks.append("local risk policy requires allowedSymbols before execution")
    if not policy.allowed_order_types:
        blocks.append("local risk policy requires allowedOrderTypes before execution")
    if policy.max_order_notional is None:
        blocks.append("local risk policy requires maxOrderNotional before execution")
    if policy.max_position_notional is None:
        blocks.append("local risk policy requires maxPositionNotional before execution")
    if policy.max_daily_loss is None:
        blocks.append("local risk policy requires maxDailyLoss before execution")
    return blocks


def _block(reason_code: str, reason: str) -> RiskDecision:
    return RiskDecision(passed=False, reason_code=reason_code, reason=reason)


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
