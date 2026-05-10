#!/usr/bin/env bash
set -euo pipefail

export PYTHONHASHSEED=0
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1788912000}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

mkdir -p dist
rm -f dist/*.whl dist/SHA256SUMS dist/.gitignore
uv build --wheel --out-dir dist
rm -f dist/.gitignore
sha256sum dist/*.whl > dist/SHA256SUMS
