# YTM Executor

Self-hosted zero-secret executor for Your Trading Manager.

YTM Executor runs in user-controlled infrastructure. It enrolls with YTM Cloud, stores broker
credentials locally, leases approved commands, and reports sanitized results. Broker secrets must
never be sent to YTM Cloud.

For the user-facing trust checklist, see `TRUST.md`. Local executor risk policy is documented in
`docs/RISK_POLICY.md`.

## Current Scope

Implemented:

- one-time enrollment with YTM Cloud;
- machine token storage on the executor host;
- local AES-GCM secret store for broker credentials;
- heartbeat with non-secret credential metadata;
- validate-only broker credential checks from the executor host;
- heartbeat with sanitized validation status, permissions summary, and account fingerprint;
- local command preflight before any future broker adapter can run;
- mandatory local fail-safe gate with kill switch, paper-only mode, YTM risk attestation,
  futures reduce-only checks, and optional local daily/total drawdown stops;
- broker adapter boundary with normalized order requests and deterministic `clientOrderId`
  fallback;
- Binance USD-M Futures mainnet `test_order` adapter through the official
  `binance-sdk-derivatives-trading-usds-futures` package;
- Binance Futures pre-trade normalization against `exchangeInfo` filters before `test_order`;
- OKX SWAP mainnet `order-precheck` adapter through `python-okx==0.4.1`;
- OKX SWAP mainnet real order adapter through `python-okx==0.4.1`, disabled unless the executor is
  started with explicit real-order enablement;
- command lease polling;
- sanitized provider reconciliation snapshot upload to YTM;
- OKX SWAP read-only reconciliation capture for balances, positions, open orders, order history,
  algo TP/SL history, fills, fill PnL, fees, and inferred close source;
- client-side rejection of secret-like fields in YTM API payloads;
- Docker-first VPS installer;
- GHCR Docker image build, cosign signing, SBOM generation, and release checksums in CI;
- tests proving locally stored broker secrets are not sent to YTM requests.

Not implemented yet:

- Binance/T-Bank real order placement adapters;
- provider-specific streaming reconciliation.

Binance adapter work uses the official Binance Python connector repository behind the executor
adapter boundary. The current pinned package is
`binance-sdk-derivatives-trading-usds-futures==10.2.0`. The first adapter targets USD-M Futures
mainnet, fetches `exchangeInfo`, normalizes price and quantity to symbol filters, checks min/max
quantity, price, and notional, and calls `test_order` only. Binance validates the request without
submitting it to the matching engine, and the executor still rejects real order placement after that
validate-only call.

OKX adapter work uses `python-okx==0.4.1`. The first OKX adapter targets SWAP mainnet,
normalizes symbol, contract size, and price against `account/instruments`, then calls
`POST /api/v5/trade/order-precheck` only. Plain USDT pairs such as `BTCUSDT` are mapped to native
OKX SWAP ids such as `BTC-USDT-SWAP`; when `quantity` is absent, contract size is derived locally
from `orderNotional` and `priceReference`. OKX validates the request without placing an order.
For `external_paper`, the executor reports a sanitized acknowledgement with
`order_placement_skipped`. For `real`, the separate `okx_swap_mainnet_order` adapter requires entry
stop-loss and take-profit, converts naked market entries into bounded IOC-limit orders with
`maxSlippageBps`, validates OKX account/position mode, verifies `set-leverage`, first calls the
same `order-precheck` when OKX supports it for the account mode, looks up the deterministic client
order id before retrying, then calls
`POST /api/v5/trade/order` only when all local gates pass and the executor was started with
`--enable-real-orders`. Attached TP/SL uses OKX `attachAlgoOrds` with market close prices. After
submit, the executor verifies active OKX TP/SL algo orders through `orders-algo-pending` with the
required `ordType` filter and reports `protectionStatus` for operator action. If the entry order
was accepted but post-submit protection verification fails, the executor still reports
`order_submitted` and marks protection as `verification_failed` instead of turning the accepted
provider order into a rejected command.

## Install On A VPS

Prerequisites:

- Ubuntu/Debian/Fedora-family VPS with `sudo`;
- outbound access to your YTM server;
- outbound access to the broker API when credential validation or future adapters are used.

One-command install from the token shown by YTM:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/v0.7.12/scripts/install.sh | sudo bash -s -- \
  --server https://trademate.pro \
  --enrollment-token ytm_enroll_xxx \
  --wizard
```

This installs Docker Compose when needed, writes `/opt/ytm-executor/docker-compose.yml`, pulls the
pinned public executor image `ghcr.io/fedortuchin/your-trading-manager-executor:v0.7.12`, enrolls it
with YTM, prompts locally for broker credentials, validates them, prompts for an optional local
risk policy, and starts the container with a persistent local volume. `ytm_enroll_xxx` is the
one-time enrollment token generated by YTM for that executor registration.

The wizard keeps broker secrets on the VPS. It does not ask users to configure per-symbol local
allowlists; symbols and daily limits are configured in the YTM trading profile before approval.
It also does not ask for leverage, notional, or order-type limits; those are app-owned YTM trading
profile settings. The local executor policy only controls kill switch, paper-only mode, and
optional local daily/total drawdown stops.

Add a broker credential later through a local prompt on the same VPS:

```bash
cd /opt/ytm-executor
sudo docker compose run --rm ytm-executor broker add --provider tbank
```

For Binance, use `--provider binance`. For OKX, use `--provider okx`; the executor will prompt for
API key, API secret, and API passphrase. Do not pass broker tokens in shell arguments.

Validate the local credential from the VPS without placing orders:

```bash
sudo docker compose run --rm ytm-executor broker validate --provider tbank
```

Validation calls only read-only broker identity/account endpoints. YTM receives only status,
permissions summary, warnings, checked time, and a hashed account fingerprint.

To add only a broker credential during the same install without the risk-policy wizard, ask the
installer to prompt on the VPS terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/v0.7.12/scripts/install.sh | sudo bash -s -- \
  --server https://trademate.pro \
  --enrollment-token ytm_enroll_xxx \
  --broker-provider tbank \
  --validate-broker
```

Do not pass broker tokens in the install command. The installer uses a local terminal prompt, so
broker secrets are written only into the encrypted local store inside the executor's Docker volume.

Docker Compose manual flow:

```bash
curl -fsSLO https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/v0.7.12/docker-compose.yml
docker compose run --rm ytm-executor enroll \
  --server-url https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
docker compose up -d
```

Python/venv install for development or power users:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/v0.7.12/scripts/install-venv.sh | sudo bash -s -- \
  --server https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
```

Manual install from a built wheel:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install \
  --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple \
  ytm-executor-0.7.12-py3-none-any.whl
```

Enroll with the token shown by YTM:

```bash
ytm-executor enroll \
  --server-url https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
```

Add a local broker credential. This stores the secret only on this VPS:

```bash
ytm-executor broker add --provider tbank --name main
```

Validate it locally:

```bash
ytm-executor broker validate --provider tbank --name main
```

OKX example:

```bash
ytm-executor broker add --provider okx --name main
ytm-executor broker validate --provider okx --name main
```

Create the mandatory local fail-safe policy. Without this file or with `killSwitch=true`,
provider-backed commands fail closed before any broker adapter can run. Trading limits such as
symbols, leverage, notional, max open trades, and approval mode live in the YTM trading profile.
The executor-local fail-safe is intentionally limited to kill switch, paper-only mode, and optional
local daily/total drawdown stops.

```bash
ytm-executor risk init --kill-switch-off \
  --max-daily-loss 250 \
  --max-total-drawdown 1000
```

For OKX SWAP real placement, also remove the local paper-only block:

```bash
ytm-executor risk init --kill-switch-off \
  --allow-real \
  --max-daily-loss 250 \
  --max-total-drawdown 1000
```

Docker install equivalent:

```bash
cd /opt/ytm-executor
sudo docker compose run --rm ytm-executor risk init --kill-switch-off \
  --allow-real \
  --max-daily-loss 250 \
  --max-total-drawdown 1000
```

Use `ytm-executor risk show` to inspect the local fail-safe. `--allow-real` only removes the local
paper-only risk block. OKX real order placement still requires the exact real adapter in the leased
command and `ytm-executor run --enable-real-orders`; without that runtime flag, `real` remains
fail-closed.

Run once:

```bash
ytm-executor run --once
```

Run continuously:

```bash
ytm-executor run
```

Continuous mode runs three independent loops:

- heartbeat every 15 seconds by default;
- command leasing through bounded long-polling, with a 25 second wait by default;
- optional reconciliation on its own interval.

Run continuously with OKX read-only reconciliation polling:

```bash
ytm-executor run --reconcile-okx --reconciliation-interval-seconds 60
```

Latency-sensitive production runs can make the command channel explicit:

```bash
ytm-executor run \
  --heartbeat-interval-seconds 15 \
  --lease-wait-seconds 25 \
  --lease-poll-interval-seconds 1 \
  --reconcile-okx \
  --reconciliation-interval-seconds 60
```

Upload a sanitized provider reconciliation snapshot from the executor host:

```bash
ytm-executor reconciliation upload-snapshot \
  --snapshot-type full \
  --status ok \
  --execution-mode external_paper \
  --payload-file reconciliation.json
```

The payload file must be a JSON object without secret-like fields. YTM stores the sanitized
snapshot and can apply matched provider orders, idempotent fills, provider-backed positions, fees,
realized PnL, and close-source labels when the payload includes exact provider fill/order data.

Capture and upload an OKX SWAP read-only snapshot directly from the executor host:

```bash
ytm-executor reconciliation capture-okx --execution-mode external_paper
```

This calls OKX `account/balance`, `account/positions`, `trade/orders-pending`,
`trade/orders-history`, `trade/order-algos-pending`, `trade/order-algos-history`, and
`trade/fills-history`, then uploads only normalized balances, positions, orders, order history,
algo order metadata, fills, fees, fill PnL, and close-source labels inferred from OKX fields such
as `actualSide=tp/sl`. Broker credentials stay in the local secret store and are not included in
the YTM payload.

When a command is leased, the executor runs local preflight before acknowledging anything:

- command payload must not contain secret-like fields;
- command provider must match a locally configured broker credential provider;
- local broker credential validation must be fresh, passed, and account-readable;
- local fail-safe policy must be configured, enabled, and kill switch off;
- `real` commands must include YTM risk attestation and pass optional local drawdown stops;
- order request normalization must produce a valid adapter request and deterministic
  `clientOrderId`;
- `external_paper` is acknowledged with `order_placement_skipped`; OKX `external_paper` may first
  run `order-precheck` when the command explicitly requests `okx_swap_mainnet_order_precheck`;
- `real` is locally rejected in this foundation build.

Rejected preflight results are sanitized and reported as `rejected` with
`executorAction=local_preflight_failed`.

## Security Model

YTM Cloud may receive:

- executor enrollment metadata;
- heartbeat status;
- command lease requests;
- sanitized execution results.
- sanitized broker credential validation status.
- sanitized provider reconciliation snapshots.
- OKX read-only reconciliation state: balances, positions, open orders, order history, algo TP/SL
  history, fills, fill PnL, fees, and inferred close source.

YTM Cloud must not receive:

- Binance API secrets;
- OKX API secrets and passphrases;
- T-Bank Invest tokens;
- private keys;
- passwords/passphrases;
- Authorization headers.

The executor rejects outgoing YTM payloads containing secret-like keys before sending them.
Broker validation may send broker credentials only to the selected broker API from the executor
host. Those credentials are never included in YTM API payloads.
Command preflight is also local and fail-closed: no adapter execution can be added later without
passing these local checks first. Local fail-safe policy is stored on the executor host and is not
controlled by YTM Cloud, so a cloud-side command cannot relax the executor's kill switch, paper-only
mode, or drawdown stops. Trading limits remain in YTM to avoid two sources of truth.
The default adapter path currently uses a disabled adapter that prepares a sanitized order request
and returns `order_placement_skipped`; it does not call a broker. Binance `real` commands
can request the `binance_usdm_futures_mainnet_order_test` adapter in `commandPayload.adapter`,
which fetches Binance USD-M Futures mainnet `exchangeInfo`, normalizes the order to exchange
filters, calls `test_order` only, and reports `order_test_validated` when Binance accepts the
validate-only request. The executor still rejects the command with `real_execution_disabled` after
this preflight because real placement is not enabled yet.
OKX `external_paper` and `real` commands can request the `okx_swap_mainnet_order_precheck` adapter
in `commandPayload.adapter`, which fetches OKX SWAP instrument rules, normalizes size and price,
calls `order-precheck` only, and reports `order_precheck_validated` when OKX accepts the
validate-only request. `external_paper` is acknowledged without placement; `real` is still rejected
with `real_execution_disabled` after this preflight.
OKX `real` commands can request the `okx_swap_mainnet_order` adapter. That adapter is disabled by
default; when the executor is started with `--enable-real-orders` and local fail-safe permits
real trading, it requires opening-order `stopLoss` and take-profit, bounds market entries as
IOC-limit orders, attaches TP/SL through OKX `attachAlgoOrds`, runs `order-precheck`, checks for an
existing `clOrdId`, then calls `trade/order` and reports `order_submitted` with sanitized
`providerOrderId` and `protectionStatus`. Post-submit TP/SL verification follows the OKX v5
`orders-algo-pending` contract by passing the required `ordType`; verification failures after an
accepted entry become `protectionStatus=verification_failed`, not command rejection.
The secret-field guard prevents common accidental leaks by rejecting secret-like field names; it is
not a substitute for source review, tests, signed releases, and network allowlisting.

Network expectations are documented in `docs/NETWORK.md`.

## Reproducible Build

```bash
scripts/build_artifact.sh
```

The script sets deterministic build environment variables and writes `dist/SHA256SUMS`.

Sign an artifact:

```bash
scripts/sign_artifact.sh dist/ytm_executor-0.7.12-py3-none-any.whl
```

The signer requires `cosign` or `minisign`.

Docker images are published to:

```text
ghcr.io/fedortuchin/your-trading-manager-executor
```

Production installs should use a version tag such as `v0.7.12` or an image digest such as
`ghcr.io/fedortuchin/your-trading-manager-executor@sha256:<digest>`, not a floating tag. Release
artifacts and SHA256 checksums are described in `RELEASES.md`.
