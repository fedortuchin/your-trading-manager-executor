#!/usr/bin/env bash
set -euo pipefail

artifact="${1:?artifact path is required}"

if command -v cosign >/dev/null 2>&1; then
  cosign sign-blob --yes "${artifact}" --output-signature "${artifact}.sig"
  exit 0
fi

if command -v minisign >/dev/null 2>&1; then
  minisign -S -m "${artifact}"
  exit 0
fi

echo "cosign or minisign is required for executor release signing" >&2
exit 1
