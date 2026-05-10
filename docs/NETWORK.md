# Network Policy

This document lists the expected network paths for a self-hosted YTM executor.

## Runtime Egress

Current foundation build:

- YTM Cloud over HTTPS:
  - default production: `https://trademate.pro`
  - custom deployments: the host passed to `ytm-executor enroll --server-url`
- No broker API egress is required because real broker adapters are not implemented yet.

The executor stores the allowed YTM host during enrollment and refuses YTM API requests to any other
host.

## Install-Time Egress

Docker-first installer may access:

- `raw.githubusercontent.com` to download `scripts/install.sh`;
- the OS package repositories used by the VPS image when Docker is missing;
- `ghcr.io` and GitHub container registry backing hosts to pull the executor image;
- the configured YTM server for enrollment.

Python/venv installer may also access:

- `github.com` to install the public source package;
- `astral.sh` to install `uv`;
- Python package indexes used by `uv`.

## Future Broker Adapter Egress

When broker adapters are enabled, runtime egress should be limited to the configured YTM server and
the selected broker API domains. Expected examples:

- Binance: Binance REST/WebSocket API domains configured for the selected market and account type.
- T-Bank Invest: T-Bank Invest API endpoints configured by the adapter.

Broker API hosts must be explicit adapter configuration, not YTM-provided secret-bearing payloads.

## Firewall Guidance

Domain-level egress control usually requires a firewall, DNS proxy, or outbound proxy that supports
host allowlists. Plain Linux `ufw`/`iptables` IP rules are brittle for GitHub, GHCR, broker APIs, and
cloud-hosted YTM domains because their IPs can change.

Minimum practical production stance:

- SSH only from trusted admin IPs;
- no inbound public ports for the executor service;
- outbound HTTPS only to the YTM host, install/update hosts, and enabled broker API hosts;
- broker-side API key restrictions such as IP allowlists and disabled withdrawals.
