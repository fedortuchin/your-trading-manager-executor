#!/usr/bin/env bash
set -euo pipefail

export PYTHONHASHSEED=0
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1788912000}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

uv build --wheel --out-dir dist
sha256sum dist/*.whl > dist/SHA256SUMS
