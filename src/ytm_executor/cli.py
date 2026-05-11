"""Command line interface for the self-hosted executor."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from ytm_executor import __version__
from ytm_executor.client import YtmClient
from ytm_executor.preflight import CommandPreflightDecision, preflight_command
from ytm_executor.risk import (
    DEFAULT_RISK_POLICY_FILE,
    DEFAULT_RISK_STATE_FILE,
    RiskPolicy,
    policy_completeness_blocks,
    read_risk_policy,
    read_risk_state,
    risk_policy_public_summary,
    risk_policy_to_file_payload,
    write_risk_policy,
)
from ytm_executor.secret_store import DEFAULT_KEY_FILE, DEFAULT_SECRETS_FILE, LocalSecretStore
from ytm_executor.state import (
    DEFAULT_STATE_FILE,
    ExecutorState,
    expect_object,
    read_state,
    write_state,
)
from ytm_executor.validation import validate_broker_credential
from ytm_executor.validation_store import DEFAULT_VALIDATIONS_FILE, LocalValidationStore

CLIENT_VERSION = f"ytm-executor/{__version__}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ytm-executor")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--secrets-file", default=str(DEFAULT_SECRETS_FILE))
    parser.add_argument("--key-file", default=str(DEFAULT_KEY_FILE))
    parser.add_argument("--validations-file", default=str(DEFAULT_VALIDATIONS_FILE))
    parser.add_argument("--risk-policy-file", default=str(DEFAULT_RISK_POLICY_FILE))
    parser.add_argument("--risk-state-file", default=str(DEFAULT_RISK_STATE_FILE))
    subparsers = parser.add_subparsers(dest="command", required=True)

    enroll_parser = subparsers.add_parser("enroll")
    enroll_parser.add_argument("--server-url", required=True)
    enroll_parser.add_argument("--enrollment-token", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--interval-seconds", default=5, type=int)

    broker_parser = subparsers.add_parser("broker")
    broker_subparsers = broker_parser.add_subparsers(dest="broker_command", required=True)
    broker_add = broker_subparsers.add_parser("add")
    broker_add.add_argument("--provider", required=True, choices=["binance", "okx", "tbank"])
    broker_add.add_argument("--name", default="main")
    broker_add.add_argument("--token")
    broker_add.add_argument("--api-key")
    broker_add.add_argument("--api-secret")
    broker_add.add_argument("--passphrase")
    broker_validate = broker_subparsers.add_parser("validate")
    broker_validate.add_argument("--provider", required=True, choices=["binance", "okx", "tbank"])
    broker_validate.add_argument("--name", default="main")
    broker_subparsers.add_parser("list")

    risk_parser = subparsers.add_parser("risk")
    risk_subparsers = risk_parser.add_subparsers(dest="risk_command", required=True)
    risk_subparsers.add_parser("show")
    risk_init = risk_subparsers.add_parser("init")
    risk_init.add_argument("--allow-market", action="append", default=[])
    risk_init.add_argument("--allow-margin-mode", action="append", default=[])
    risk_init.add_argument("--allow-symbol", action="append", default=[])
    risk_init.add_argument("--allow-order-type", action="append", default=[])
    risk_init.add_argument("--max-order-notional")
    risk_init.add_argument("--max-position-notional")
    risk_init.add_argument(
        "--max-symbol-notional",
        action="append",
        default=[],
        help="Per-symbol exposure limit in SYMBOL=VALUE format.",
    )
    risk_init.add_argument("--max-daily-loss")
    risk_init.add_argument("--max-leverage", default="1")
    risk_init.add_argument("--position-mode", default="one_way", choices=["one_way"])
    risk_init.add_argument("--allow-real", action="store_true")
    risk_init.add_argument("--kill-switch-off", action="store_true")
    risk_init.add_argument("--force", action="store_true")

    reconciliation_parser = subparsers.add_parser("reconciliation")
    reconciliation_subparsers = reconciliation_parser.add_subparsers(
        dest="reconciliation_command",
        required=True,
    )
    reconciliation_upload = reconciliation_subparsers.add_parser("upload-snapshot")
    reconciliation_upload.add_argument("--snapshot-type", required=True)
    reconciliation_upload.add_argument("--status", required=True)
    reconciliation_upload.add_argument("--payload-file", required=True)
    reconciliation_upload.add_argument("--execution-mode")
    reconciliation_upload.add_argument("--provider-snapshot-id")

    args = parser.parse_args(argv)
    try:
        if args.command == "enroll":
            return _enroll(args)
        if args.command == "run":
            return _run(args)
        if args.command == "broker":
            return _broker(args)
        if args.command == "risk":
            return _risk(args)
        if args.command == "reconciliation":
            return _reconciliation(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


def _enroll(args: argparse.Namespace) -> int:
    server_url = _normalized_server_url(args.server_url)
    host = _required_host(server_url)
    client = YtmClient(server_url=server_url, allowed_hosts=(host,))
    response = client.enroll(
        enrollment_token=args.enrollment_token,
        client_version=CLIENT_VERSION,
        capabilities={"leases": True, "zeroSecret": True},
        allowed_egress={"ytmApi": host},
    )
    executor = expect_object(response, "executor")
    access_token = _expect_text(response, "accessToken")
    executor_id = _expect_text(executor, "id")
    write_state(
        Path(args.state_file),
        ExecutorState(
            access_token=access_token,
            allowed_hosts=(host,),
            executor_id=executor_id,
            server_url=server_url,
        ),
    )
    print("executor enrolled")
    return 0


def _run(args: argparse.Namespace) -> int:
    state = read_state(Path(args.state_file))
    store = _store(args)
    validation_store = _validation_store(args)
    client = YtmClient(server_url=state.server_url, allowed_hosts=state.allowed_hosts)
    while True:
        risk_policy = read_risk_policy(Path(args.risk_policy_file))
        capabilities = {"leases": True, "zeroSecret": True}
        capabilities.update(
            store.heartbeat_capability(
                validation_summaries=validation_store.list_public(),
            )
        )
        capabilities["localRiskPolicy"] = risk_policy_public_summary(risk_policy)
        client.heartbeat(
            access_token=state.access_token,
            capabilities=capabilities,
            client_version=CLIENT_VERSION,
        )
        lease_response = client.lease_command(access_token=state.access_token)
        item = lease_response.get("item")
        if isinstance(item, dict):
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
            decision = preflight_command(
                item,
                local_credentials=store.list(),
                local_secret_resolver=lambda provider, name: store.get(
                    provider=provider,
                    name=name,
                ),
                risk_policy=risk_policy,
                risk_state=read_risk_state(Path(args.risk_state_file)),
                validation_summaries=validation_store.list_public(),
            )
            _record_preflight_decision(client, state.access_token, item, decision)
        if args.once:
            return 0
        time.sleep(max(1, int(args.interval_seconds)))


def _broker(args: argparse.Namespace) -> int:
    store = _store(args)
    validation_store = _validation_store(args)
    if args.broker_command == "add":
        secret = _broker_secret(args)
        store.put(provider=args.provider, name=args.name, secret=secret)
        print("broker credential stored locally")
        return 0
    if args.broker_command == "validate":
        secret = store.get(provider=args.provider, name=args.name)
        summary = validate_broker_credential(
            provider=args.provider,
            name=args.name,
            secret=secret,
        )
        validation_store.put(summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary.get("status") == "passed" else 1
    if args.broker_command == "list":
        for item in store.list():
            print(f"{item.provider}\t{item.name}")
        return 0
    return 2


def _risk(args: argparse.Namespace) -> int:
    policy_file = Path(args.risk_policy_file)
    if args.risk_command == "show":
        policy = read_risk_policy(policy_file)
        payload = risk_policy_to_file_payload(policy)
        payload["configured"] = policy.configured
        payload["completenessBlocks"] = policy_completeness_blocks(policy)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.risk_command == "init":
        if policy_file.exists() and not args.force:
            raise ValueError("risk policy already exists; use --force to overwrite it")
        policy = _risk_policy_from_args(args)
        write_risk_policy(policy_file, policy)
        print("local risk policy written")
        return 0
    return 2


def _reconciliation(args: argparse.Namespace) -> int:
    if args.reconciliation_command == "upload-snapshot":
        state = read_state(Path(args.state_file))
        client = YtmClient(server_url=state.server_url, allowed_hosts=state.allowed_hosts)
        payload = _read_json_object(Path(args.payload_file), "payload-file")
        response = client.record_reconciliation_snapshot(
            access_token=state.access_token,
            execution_mode=args.execution_mode,
            payload=payload,
            provider_snapshot_id=args.provider_snapshot_id,
            snapshot_type=args.snapshot_type,
            status=args.status,
        )
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0
    return 2


def _broker_secret(args: argparse.Namespace) -> dict[str, str]:
    if args.provider == "tbank":
        token = args.token or getpass.getpass("T-Bank Invest token: ")
        return {"token": _required_text(token, "token")}
    label = "OKX" if args.provider == "okx" else "Binance"
    api_key = args.api_key or input(f"{label} API key: ").strip()
    api_secret = args.api_secret or getpass.getpass(f"{label} API secret: ")
    if args.provider == "okx":
        passphrase = args.passphrase or getpass.getpass("OKX API passphrase: ")
        return {
            "apiKey": _required_text(api_key, "api_key"),
            "apiSecret": _required_text(api_secret, "api_secret"),
            "passphrase": _required_text(passphrase, "passphrase"),
        }
    return {
        "apiKey": _required_text(api_key, "api_key"),
        "apiSecret": _required_text(api_secret, "api_secret"),
    }


def _risk_policy_from_args(args: argparse.Namespace) -> RiskPolicy:
    from decimal import Decimal

    policy = RiskPolicy(
        configured=True,
        enabled=True,
        kill_switch=not args.kill_switch_off,
        paper_only=not args.allow_real,
        allowed_markets=tuple(_unique_lower_text(args.allow_market)),
        allowed_margin_modes=tuple(_unique_lower_text(args.allow_margin_mode)),
        allowed_symbols=tuple(_unique_text(args.allow_symbol, upper=True)),
        allowed_order_types=tuple(_unique_order_type(args.allow_order_type)),
        max_order_notional=_optional_decimal_arg(args.max_order_notional, "max-order-notional"),
        max_position_notional=_optional_decimal_arg(
            args.max_position_notional,
            "max-position-notional",
        ),
        max_symbol_notional=_symbol_notional_limits(args.max_symbol_notional),
        max_daily_loss=_optional_decimal_arg(args.max_daily_loss, "max-daily-loss"),
        max_leverage=Decimal(str(args.max_leverage)),
        position_mode=str(args.position_mode).strip().lower(),
    )
    if not policy.kill_switch:
        blocks = policy_completeness_blocks(policy)
        if blocks:
            raise ValueError("; ".join(blocks))
    return policy


def _optional_decimal_arg(value: object, name: str):
    if value is None:
        return None
    return Decimal(str(value))


def _unique_text(values: list[str], *, upper: bool) -> list[str]:
    result = []
    seen = set()
    for value in values:
        normalized = value.strip()
        if upper:
            normalized = normalized.upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _unique_order_type(values: list[str]) -> list[str]:
    return _unique_text([value.lower().replace("-", "_") for value in values], upper=False)


def _unique_lower_text(values: list[str]) -> list[str]:
    return _unique_text([value.lower().replace("-", "_") for value in values], upper=False)


def _symbol_notional_limits(values: list[str]):
    from decimal import Decimal

    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("max-symbol-notional must use SYMBOL=VALUE format")
        symbol, limit = value.split("=", 1)
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or not limit.strip():
            raise ValueError("max-symbol-notional must use SYMBOL=VALUE format")
        result[normalized_symbol] = Decimal(limit.strip())
    return result


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _store(args: argparse.Namespace) -> LocalSecretStore:
    return LocalSecretStore(
        key_file=Path(args.key_file),
        secrets_file=Path(args.secrets_file),
    )


def _validation_store(args: argparse.Namespace) -> LocalValidationStore:
    return LocalValidationStore(validations_file=Path(args.validations_file))


def _record_preflight_decision(
    client: YtmClient,
    access_token: str,
    item: dict[str, object],
    decision: CommandPreflightDecision,
) -> None:
    command = expect_object(item, "command")
    lease = expect_object(item, "lease")
    client.record_command_result(
        access_token=access_token,
        command_id=_expect_text(command, "id"),
        lease_id=_expect_text(lease, "id"),
        status=decision.status,
        result_payload=decision.result_payload,
    )


def _normalized_server_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("server URL must be http(s)")
    return value.rstrip("/")


def _required_host(server_url: str) -> str:
    host = urlparse(server_url).hostname
    if not host:
        raise ValueError("server URL host is required")
    return host


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _expect_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is missing")
    return value


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
