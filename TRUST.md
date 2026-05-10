# Trust Model

YTM Executor is open-source and self-hosted. Broker credentials are entered on the user's executor
host and must not be sent to YTM Cloud.

## What YTM Cloud Can See

- executor enrollment metadata;
- executor heartbeat status and client version;
- broker provider name and local credential label;
- sanitized broker credential validation status;
- command lease requests;
- approved commands created by YTM;
- sanitized executor results and preflight rejection reasons.
- local risk policy summary counts and mode flags, not the full local policy.

## What YTM Cloud Must Not See

- Binance API secrets;
- T-Bank Invest tokens;
- Authorization headers;
- private keys;
- passwords or passphrases;
- broker credential plaintext.
- local broker credential files or local risk state files.

## How To Verify

1. Inspect the public source code in this repository.
2. Install from a pinned release tag, for example `v0.3.0`, not from `main`.
3. Prefer a pinned image digest for production:

   ```text
   ghcr.io/fedortuchin/your-trading-manager-executor@sha256:<digest>
   ```

4. Verify release checksums:

   ```bash
   sha256sum -c SHA256SUMS
   ```

5. Verify the Docker image signature with cosign:

   ```bash
   cosign verify ghcr.io/fedortuchin/your-trading-manager-executor:v0.3.0 \
     --certificate-identity-regexp 'https://github.com/fedortuchin/your-trading-manager-executor/.github/workflows/ci.yml@.*' \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com
   ```

6. Review `docs/NETWORK.md` and run the executor with outbound allowlisting where possible.
7. Restrict broker-side API credentials with IP allowlists and disabled withdrawals.
8. Keep the local executor risk policy in source-controlled infrastructure or another audited
   local change process. YTM Cloud can request an order, but it cannot disable the executor's local
   kill switch or raise local limits.

## Honest Limits

- Local AES-GCM storage protects against accidental file disclosure, not full compromise of the
  executor host. If an attacker controls the VPS, they can read the local master key and decrypt
  local broker credentials.
- Secret-field rejection is a guardrail against common accidental leaks. It checks field names like
  `apiKey`, `apiSecret`, `token`, and `Authorization`; it is not a formal proof that arbitrary text
  can never contain a secret.
- Signed releases and SBOMs improve supply-chain verification, but users still need source review,
  pinned images, network allowlisting, and hardened infrastructure.
- The current foundation build has no real order adapters. `real` commands are locally rejected by
  executor preflight.
- The local risk policy is mandatory for provider-backed command preflight. Missing policy,
  `killSwitch=true`, incomplete limits, unsupported symbol/order type, excessive notional,
  excessive projected position, daily loss breach, or excessive leverage all fail closed before any
  adapter can run.
