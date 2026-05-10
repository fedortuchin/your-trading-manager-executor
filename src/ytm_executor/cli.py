"""Command line interface for the self-hosted executor."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from ytm_executor import __version__
from ytm_executor.client import YtmClient
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
    broker_add.add_argument("--provider", required=True, choices=["binance", "tbank"])
    broker_add.add_argument("--name", default="main")
    broker_add.add_argument("--token")
    broker_add.add_argument("--api-key")
    broker_add.add_argument("--api-secret")
    broker_validate = broker_subparsers.add_parser("validate")
    broker_validate.add_argument("--provider", required=True, choices=["binance", "tbank"])
    broker_validate.add_argument("--name", default="main")
    broker_subparsers.add_parser("list")

    args = parser.parse_args(argv)
    try:
        if args.command == "enroll":
            return _enroll(args)
        if args.command == "run":
            return _run(args)
        if args.command == "broker":
            return _broker(args)
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
        capabilities = {"leases": True, "zeroSecret": True}
        capabilities.update(
            store.heartbeat_capability(
                validation_summaries=validation_store.list_public(),
            )
        )
        client.heartbeat(
            access_token=state.access_token,
            capabilities=capabilities,
            client_version=CLIENT_VERSION,
        )
        lease_response = client.lease_command(access_token=state.access_token)
        item = lease_response.get("item")
        if isinstance(item, dict):
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
            _acknowledge_without_order_placement(client, state.access_token, item)
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


def _broker_secret(args: argparse.Namespace) -> dict[str, str]:
    if args.provider == "tbank":
        token = args.token or getpass.getpass("T-Bank Invest token: ")
        return {"token": _required_text(token, "token")}
    api_key = args.api_key or input("Binance API key: ").strip()
    api_secret = args.api_secret or getpass.getpass("Binance API secret: ")
    return {
        "apiKey": _required_text(api_key, "api_key"),
        "apiSecret": _required_text(api_secret, "api_secret"),
    }


def _store(args: argparse.Namespace) -> LocalSecretStore:
    return LocalSecretStore(
        key_file=Path(args.key_file),
        secrets_file=Path(args.secrets_file),
    )


def _validation_store(args: argparse.Namespace) -> LocalValidationStore:
    return LocalValidationStore(validations_file=Path(args.validations_file))


def _acknowledge_without_order_placement(
    client: YtmClient,
    access_token: str,
    item: dict[str, object],
) -> None:
    command = expect_object(item, "command")
    lease = expect_object(item, "lease")
    client.record_command_result(
        access_token=access_token,
        command_id=_expect_text(command, "id"),
        lease_id=_expect_text(lease, "id"),
        status="acknowledged",
        result_payload={
            "executorAction": "order_placement_skipped",
            "reason": "broker adapters are not enabled in this foundation build",
            "zeroSecret": True,
        },
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
