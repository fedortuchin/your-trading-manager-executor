# Network Policy

This document lists the expected network paths for a self-hosted YTM executor.

## Runtime Egress

Current foundation build:

- YTM Cloud over HTTPS:
  - default production: `https://trademate.pro`
  - custom deployments: the host passed to `ytm-executor enroll --server-url`
- Optional validate-only broker API egress when the user runs `ytm-executor broker validate`:
  - Binance USD-M Futures REST: `https://fapi.binance.com`
  - OKX REST: `https://www.okx.com`
  - T-Bank Invest gRPC: `invest-public-api.tinkoff.ru:443`
- Optional provider-backed validate-only adapter egress when a command explicitly requests an
  enabled precheck adapter:
  - Binance USD-M Futures mainnet REST: `https://fapi.binance.com`
  - OKX SWAP mainnet REST when a command explicitly requests `okx_swap_mainnet_order_precheck`:
    `https://www.okx.com`
- Optional OKX real-order adapter egress when a `real` command explicitly requests
  `okx_swap_mainnet_order` and the executor is started with `--enable-real-orders`: OKX SWAP
  mainnet `account/instruments`, `trade/order-precheck`, and `trade/order`.
- Continuous `ytm-executor run` only needs the configured YTM server until broker adapters are
  enabled for a command, except for explicit validate-only adapter calls such as Binance USD-M
  Futures mainnet `exchangeInfo` plus `test_order` or OKX SWAP mainnet `account/instruments` plus
  `trade/order-precheck`, and explicit OKX real-order adapter calls.
- `ytm-executor reconciliation capture-okx` and `ytm-executor run --reconcile-okx` also need OKX
  SWAP read-only REST access to `account/balance`, `account/positions`, and
  `trade/orders-pending`, `trade/orders-history`, and `trade/fills-history`.
- Reconciliation snapshot upload sends sanitized provider state only to the configured YTM server.
- Local risk policy and risk state are read from the executor host filesystem and do not require
  network access. YTM receives only sanitized risk summary counts and mode flags in heartbeat.

The executor stores the allowed YTM host during enrollment and refuses YTM API requests to any other
host.

## Install-Time Egress

Docker-first installer may access:

- `raw.githubusercontent.com` to download `scripts/install.sh`;
- the OS package repositories used by the VPS image when Docker is missing;
- `ghcr.io` and GitHub container registry backing hosts to pull the executor image;
- the configured YTM server for enrollment.
- `opensource.tbank.ru` when building/installing the Python package with the official T-Bank SDK
  dependency.

Python/venv installer may also access:

- `github.com` to install the public source package;
- `astral.sh` to install `uv`;
- Python package indexes used by `uv`.

## Broker Adapter Egress

Broker validation and future broker adapters must be limited to the configured YTM server and the
selected broker API domains. Expected examples:

- Binance: Binance REST/WebSocket API domains configured for the selected market and account type.
  Future Binance adapters should use the official Binance Python connector repository behind the
  executor adapter boundary. The current Binance USD-M Futures mainnet adapter uses the official
  `binance-sdk-derivatives-trading-usds-futures==10.2.0` package, reads `exchangeInfo` for
  pre-trade normalization, and calls `test_order`, not `new_order`.
- OKX: OKX REST API domain for the user's registered region. The current OKX SWAP mainnet adapter
  uses `python-okx==0.4.1`, reads `account/instruments`, and calls `trade/order-precheck`.
  The disabled-by-default real adapter also calls `trade/order` after precheck when
  `--enable-real-orders` is set. Reconciliation uses read-only account/order/fill endpoints:
  `account/balance`, `account/positions`, `trade/orders-pending`, `trade/orders-history`, and
  `trade/fills-history`. The first build uses the standard `https://www.okx.com` domain; EU, US,
  AU, or other regional OKX accounts may need a future explicit domain setting.
- T-Bank Invest: T-Bank Invest API endpoints configured by the adapter.

Broker API hosts must be explicit adapter configuration, not YTM-provided secret-bearing payloads.
YTM heartbeat may receive sanitized validation status, but never broker credentials.

## Firewall Guidance

Domain-level egress control usually requires a firewall, DNS proxy, or outbound proxy that supports
host allowlists. Plain Linux `ufw`/`iptables` IP rules are brittle for GitHub, GHCR, broker APIs, and
cloud-hosted YTM domains because their IPs can change.

Minimum practical production stance:

- SSH only from trusted admin IPs;
- no inbound public ports for the executor service;
- outbound HTTPS only to the YTM host, install/update hosts, and enabled broker API hosts;
- broker-side API key restrictions such as IP allowlists and disabled withdrawals.
