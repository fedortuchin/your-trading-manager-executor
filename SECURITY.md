# Security Policy

## Core Rule

Broker credentials stay on the executor host. YTM Cloud must not receive broker API secrets,
T-Bank Invest tokens, private keys, passphrases, passwords, or Authorization headers.

## Threat Model

Protected assets:

- broker API tokens and exchange API key material;
- YTM executor machine token;
- approved command stream;
- local execution results before they are sanitized.

Trusted components:

- the user-controlled VPS or host running the executor;
- the public executor source code and signed release artifacts;
- YTM Cloud only for approvals, command leases, audit, and sanitized status.

Not trusted with broker secrets:

- YTM Cloud;
- browser sessions;
- support staff;
- GitHub Actions logs and release metadata;
- shell command history and process lists.

Primary controls:

- broker credentials are entered through local prompts, not YTM UI forms;
- broker credentials are stored only in the executor local encrypted store;
- broker credential validation runs from the executor host and stores only sanitized status,
  warnings, permissions summary, and hashed account fingerprint;
- leased commands pass local preflight before any future broker adapter can run;
- YTM API payloads are rejected client-side when secret-like fields are present;
- YTM server APIs also reject secret-like result and metadata fields;
- Docker runtime uses a non-root user, persistent local volume, dropped Linux capabilities, and
  `no-new-privileges`;
- release artifacts include SHA256 checksums, image signing, and SBOM output.

Known limits:

- local encrypted storage protects against accidental disclosure, not full compromise of the VPS;
- users should harden SSH access, backups, host firewall rules, and broker-side API restrictions;
- no real broker order adapters are enabled in the current foundation build.
- local preflight currently rejects all `real` commands even if YTM leases one by mistake.
- validate-only broker API calls still require users to trust and harden the executor host because
  that host can decrypt local broker credentials at runtime.

## Verification

Users and auditors can verify this by checking:

- `src/ytm_executor/client.py`: rejects secret-like fields before any request to YTM.
- `src/ytm_executor/validation.py`: calls broker read/identity endpoints and returns only sanitized
  validation summaries.
- `src/ytm_executor/preflight.py`: rejects unsafe leased commands locally before order adapters.
- `tests/test_no_secret_egress.py`: stores fake broker secrets locally and verifies YTM payloads do
  not contain them.
- Release artifacts are built with `scripts/build_artifact.sh` and can be signed with
  `scripts/sign_artifact.sh`.
- Docker images are published by GitHub Actions, signed with cosign, and accompanied by SBOM output.
- Network expectations are documented in `docs/NETWORK.md`.

## Reporting

Report security issues privately to the repository owner. Do not open public issues containing
secrets, tokens, exploit payloads, or logs with credentials.
