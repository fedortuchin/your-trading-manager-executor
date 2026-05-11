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
cosign verify ghcr.io/fedortuchin/your-trading-manager-executor:v0.7.0 \
  --certificate-identity-regexp 'https://github.com/fedortuchin/your-trading-manager-executor/.github/workflows/ci.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

For production installs, prefer a release tag or immutable digest:

```text
ghcr.io/fedortuchin/your-trading-manager-executor:v0.7.0
ghcr.io/fedortuchin/your-trading-manager-executor@sha256:<digest>
```

The Docker digest is shown in the GitHub Actions image job and GHCR package metadata.

## Publishing

Create a release by pushing a version tag. Use `git tag -s` when a maintainer signing key is
available; otherwise use an annotated tag and rely on cosign-signed Docker images plus release
checksums.

```bash
git tag -a v0.7.0 -m "v0.7.0"
git push origin v0.7.0
```

The GitHub Actions workflow publishes the image, signs it, generates an SBOM, and attaches release
artifacts for `v*` tags.
