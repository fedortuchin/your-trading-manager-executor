# Security Policy

## Core Rule

Broker credentials stay on the executor host. YTM Cloud must not receive broker API secrets,
T-Bank Invest tokens, private keys, passphrases, passwords, or Authorization headers.

## Verification

Users and auditors can verify this by checking:

- `src/ytm_executor/client.py`: rejects secret-like fields before any request to YTM.
- `tests/test_no_secret_egress.py`: stores fake broker secrets locally and verifies YTM payloads do
  not contain them.
- Release artifacts are built with `scripts/build_artifact.sh` and can be signed with
  `scripts/sign_artifact.sh`.

## Reporting

Report security issues privately to the repository owner. Do not open public issues containing
secrets, tokens, exploit payloads, or logs with credentials.
