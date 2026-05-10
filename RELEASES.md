# Releases

YTM Executor releases should be verifiable without trusting YTM Cloud with broker secrets.

## Artifacts

Each tagged release should publish:

- Python wheel;
- `SHA256SUMS`;
- Docker image in GitHub Container Registry;
- cosign signature for the Docker image;
- SBOM in SPDX JSON format.

The CI workflow builds deterministic wheel artifacts with:

```bash
scripts/build_artifact.sh
```

The same workflow refreshes `SHA256SUMS` before attaching artifacts to GitHub Releases.

## Verification

Verify a downloaded wheel:

```bash
sha256sum -c SHA256SUMS
```

Verify a signed Docker image after installing `cosign`:

```bash
cosign verify ghcr.io/fedortuchin/your-trading-manager-executor:<tag> \
  --certificate-identity-regexp 'https://github.com/fedortuchin/your-trading-manager-executor/.github/workflows/ci.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Publishing

Create a release by pushing a signed version tag:

```bash
git tag -s v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The GitHub Actions workflow publishes the image, signs it, generates an SBOM, and attaches release
artifacts for `v*` tags.
