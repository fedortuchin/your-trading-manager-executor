#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="ytm-executor"
SERVICE_GROUP="ytm-executor"
SERVICE_HOME="/home/${SERVICE_USER}"
INSTALL_DIR="/opt/ytm-executor"
REPO_URL="https://github.com/fedortuchin/your-trading-manager-executor.git"
REPO_REF="main"
PYTHON_VERSION="3.13"
SERVER_URL=""
ENROLLMENT_TOKEN=""
BROKER_PROVIDER=""
START_SERVICE="true"
T_BANK_PYPI_URL="https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple"

usage() {
  cat >&2 <<'EOF'
Usage:
  install-venv.sh --server <url> --enrollment-token <token> [options]

Options:
  --broker-provider <tbank|binance>  Prompt locally for broker credentials after enrollment.
  --install-dir <path>              Default: /opt/ytm-executor.
  --repo-url <url>                  Default: public GitHub executor repo.
  --ref <git-ref>                   Default: main.
  --python <version>                Default: 3.13.
  --no-start                        Install and enroll, but do not start systemd service.
  -h, --help                        Show this help.
EOF
}

log() {
  printf '[ytm-executor] %s\n' "$*"
}

fail() {
  printf '[ytm-executor] ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server|--server-url)
      SERVER_URL="${2:-}"
      shift 2
      ;;
    --enrollment-token)
      ENROLLMENT_TOKEN="${2:-}"
      shift 2
      ;;
    --broker-provider)
      BROKER_PROVIDER="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --ref)
      REPO_REF="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_VERSION="${2:-}"
      shift 2
      ;;
    --no-start)
      START_SERVICE="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "$(id -u)" == "0" ]] || fail "run through sudo/root"
[[ -n "$SERVER_URL" ]] || fail "--server is required"
[[ -n "$ENROLLMENT_TOKEN" ]] || fail "--enrollment-token is required"
case "$BROKER_PROVIDER" in
  ""|"tbank"|"binance") ;;
  *) fail "--broker-provider must be tbank or binance" ;;
esac

install_prerequisites() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl git
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl git
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl git
  else
    fail "supported package manager not found; install ca-certificates, curl, and git first"
  fi
}

install_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi
  tmp_dir="$(mktemp -d)"
  curl -fsSL https://astral.sh/uv/install.sh -o "${tmp_dir}/uv-install.sh"
  UV_INSTALL_DIR=/usr/local/bin sh "${tmp_dir}/uv-install.sh"
  rm -rf "$tmp_dir"
  UV_BIN="/usr/local/bin/uv"
  [[ -x "$UV_BIN" ]] || fail "uv install failed"
}

ensure_user() {
  if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
  fi
  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$SERVICE_HOME" \
      --gid "$SERVICE_GROUP" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0755 "$INSTALL_DIR"
  install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 "${SERVICE_HOME}/.ytm-executor"
}

as_executor() {
  runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" "$@"
}

install_executor() {
  as_executor "$UV_BIN" python install "$PYTHON_VERSION"
  as_executor "$UV_BIN" venv --python "$PYTHON_VERSION" "${INSTALL_DIR}/.venv"
  as_executor "$UV_BIN" pip install --python "${INSTALL_DIR}/.venv/bin/python" --upgrade \
    --extra-index-url "$T_BANK_PYPI_URL" \
    "git+${REPO_URL}@${REPO_REF}"
}

enroll_executor() {
  as_executor "${INSTALL_DIR}/.venv/bin/ytm-executor" enroll \
    --server-url "$SERVER_URL" \
    --enrollment-token "$ENROLLMENT_TOKEN"
}

configure_broker() {
  [[ -n "$BROKER_PROVIDER" ]] || return
  [[ -r /dev/tty ]] || fail "broker credential prompt requires an interactive terminal"
  log "prompting locally for ${BROKER_PROVIDER} credentials"
  runuser -u "$SERVICE_USER" -- env HOME="$SERVICE_HOME" \
    "${INSTALL_DIR}/.venv/bin/ytm-executor" broker add --provider "$BROKER_PROVIDER" < /dev/tty
}

write_service() {
  cat >/etc/systemd/system/ytm-executor.service <<EOF
[Unit]
Description=YTM self-hosted executor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
Environment=HOME=${SERVICE_HOME}
ExecStart=${INSTALL_DIR}/.venv/bin/ytm-executor run
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${SERVICE_HOME}/.ytm-executor

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  if [[ "$START_SERVICE" == "true" ]]; then
    systemctl enable --now ytm-executor
  else
    systemctl enable ytm-executor
  fi
}

log "installing prerequisites"
install_prerequisites
log "installing uv runtime manager"
install_uv
log "creating locked-down executor user"
ensure_user
log "installing executor from ${REPO_URL}@${REPO_REF}"
install_executor
log "enrolling executor with YTM"
enroll_executor
configure_broker
log "installing systemd service"
write_service
log "done"
