# YTM Executor

Self-hosted zero-secret executor for Your Trading Manager.

YTM Executor runs in user-controlled infrastructure. It enrolls with YTM Cloud, stores broker
credentials locally, leases approved commands, and reports sanitized results. Broker secrets must
never be sent to YTM Cloud.

## Current Scope

Implemented:

- one-time enrollment with YTM Cloud;
- machine token storage on the executor host;
- local AES-GCM secret store for broker credentials;
- heartbeat with non-secret credential metadata;
- command lease polling;
- client-side rejection of secret-like fields in YTM API payloads;
- Docker-first VPS installer;
- GHCR Docker image build, cosign signing, SBOM generation, and release checksums in CI;
- tests proving locally stored broker secrets are not sent to YTM requests.

Not implemented yet:

- Binance/T-Bank order adapters;
- real order placement;
- reconciliation/fill upload.

## Install On A VPS

Prerequisites:

- Ubuntu/Debian/Fedora-family VPS with `sudo`;
- outbound access to your YTM server;
- outbound access to the broker API only when adapters are added.

One-command install from the token shown by YTM:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/scripts/install.sh | sudo bash -s -- \
  --server https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
```

This installs Docker Compose when needed, writes `/opt/ytm-executor/docker-compose.yml`, pulls the
public executor image, enrolls it with YTM, and starts the container with a persistent local volume.

To add a broker credential during the same install, ask the installer to prompt on the VPS terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/scripts/install.sh | sudo bash -s -- \
  --server https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx \
  --broker-provider tbank
```

Do not pass broker tokens in the install command. The installer uses a local terminal prompt, so
broker secrets are written only into the encrypted local store inside the executor's Docker volume.

Docker Compose manual flow:

```bash
curl -fsSLO https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/docker-compose.yml
docker compose run --rm ytm-executor enroll \
  --server-url https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
docker compose up -d
```

Python/venv install for development or power users:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/scripts/install-venv.sh | sudo bash -s -- \
  --server https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx
```

Manual install from a built wheel:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install ytm-executor-0.1.0-py3-none-any.whl
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

Run once:

```bash
ytm-executor run --once
```

Run continuously:

```bash
ytm-executor run
```

## Security Model

YTM Cloud may receive:

- executor enrollment metadata;
- heartbeat status;
- command lease requests;
- sanitized execution results.

YTM Cloud must not receive:

- Binance API secrets;
- T-Bank Invest tokens;
- private keys;
- passwords/passphrases;
- Authorization headers.

The executor rejects outgoing YTM payloads containing secret-like keys before sending them.

Network expectations are documented in `docs/NETWORK.md`.

## Reproducible Build

```bash
scripts/build_artifact.sh
```

The script sets deterministic build environment variables and writes `dist/SHA256SUMS`.

Sign an artifact:

```bash
scripts/sign_artifact.sh dist/ytm_executor-0.1.0-py3-none-any.whl
```

The signer requires `cosign` or `minisign`.

Docker images are published to:

```text
ghcr.io/fedortuchin/your-trading-manager-executor
```

Release artifacts and SHA256 checksums are described in `RELEASES.md`.
