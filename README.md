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

This creates a locked-down `ytm-executor` system user, installs Python 3.13 through `uv`, installs
the executor from this public repository, enrolls it with YTM, and starts the `ytm-executor`
systemd service.

To add a broker credential during the same install, ask the installer to prompt on the VPS terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/scripts/install.sh | sudo bash -s -- \
  --server https://your-ytm-domain.example \
  --enrollment-token ytm_enroll_xxx \
  --broker-provider tbank
```

Do not pass broker tokens in the install command. The installer uses a local terminal prompt, so
broker secrets are written only into the encrypted local store under `/home/ytm-executor`.

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
